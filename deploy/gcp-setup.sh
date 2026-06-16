#!/usr/bin/env bash
# deploy/gcp-setup.sh
# ─────────────────────────────────────────────────────────────────────────────
# ONE-TIME setup script. Run this locally ONCE before the first CI/CD push.
# After this, GitHub Actions handles all future deploys automatically.
#
# What this does:
#   1. Enables required GCP APIs
#   2. Creates a deploy service account and downloads its key
#   3. Stores Supabase secrets in Secret Manager (so they never touch GCR logs)
#   4. Reserves a static IP and creates SSL cert for your custom domain
#   5. Creates Cloud Run services + Load Balancer routing
#   6. Prints exactly which GitHub Secrets to add
#
# Prerequisites:
#   brew install google-cloud-sdk     # or apt-get install google-cloud-sdk
#   gcloud auth login
#   gcloud config set project YOUR_PROJECT_ID
#
# Usage:
#   export PROJECT_ID=your-gcp-project-id
#   export REGION=us-central1             # Cloud Run region
#   export DOMAIN=yourdomain.com          # your custom domain (no https://)
#   export SUPABASE_URL=https://...
#   export SUPABASE_KEY=service_role_key
#   export TELEGRAM_BOT_TOKEN=...
#   bash deploy/gcp-setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${REGION:=${REGION:-us-central1}}"
: "${DOMAIN:?Set DOMAIN (e.g. sargazo.do)}"
: "${SUPABASE_URL:?Set SUPABASE_URL}"
: "${SUPABASE_KEY:?Set SUPABASE_KEY}"
: "${TELEGRAM_BOT_TOKEN:?Set TELEGRAM_BOT_TOKEN}"

API_BASE_URL="https://api.${DOMAIN}"

echo "════════════════════════════════════════════"
echo " sargapp — GCP one-time setup"
echo " Project: $PROJECT_ID  Region: $REGION"
echo " Domain:  $DOMAIN"
echo "════════════════════════════════════════════"

# ── 1. Enable APIs ────────────────────────────────────────────────────────────
echo ""
echo "▶ Enabling APIs…"
gcloud services enable \
  run.googleapis.com \
  containerregistry.googleapis.com \
  secretmanager.googleapis.com \
  compute.googleapis.com \
  --project="$PROJECT_ID"

# ── 2. Deploy service account ─────────────────────────────────────────────────
echo ""
echo "▶ Creating deploy service account…"
SA_NAME="sargapp-github-deploy"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create "$SA_NAME" \
  --display-name="Sargapp GitHub Actions deploy" \
  --project="$PROJECT_ID" 2>/dev/null || echo "  SA already exists."

for ROLE in \
  roles/run.admin \
  roles/storage.admin \
  roles/artifactregistry.writer \
  roles/secretmanager.secretAccessor \
  roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="$ROLE" \
    --condition=None --quiet 2>/dev/null || true
done

KEY_FILE="/tmp/sargapp-deploy-key.json"
gcloud iam service-accounts keys create "$KEY_FILE" \
  --iam-account="$SA_EMAIL" \
  --project="$PROJECT_ID"
echo "  ✅ Key saved to $KEY_FILE"

# ── 3. Store secrets in Secret Manager ───────────────────────────────────────
echo ""
echo "▶ Storing secrets in Secret Manager…"
_upsert_secret() {
  local name=$1 value=$2
  echo -n "$value" | gcloud secrets create "$name" \
    --data-file=- --project="$PROJECT_ID" 2>/dev/null || \
  echo -n "$value" | gcloud secrets versions add "$name" \
    --data-file=- --project="$PROJECT_ID"
  echo "  ✅ $name"
}

_upsert_secret "sargapp-supabase-url"       "$SUPABASE_URL"
_upsert_secret "sargapp-supabase-key"       "$SUPABASE_KEY"
_upsert_secret "sargapp-telegram-bot-token" "$TELEGRAM_BOT_TOKEN"
_upsert_secret "sargapp-api-base-url"       "$API_BASE_URL"

# Grant the deploy SA access to read secrets at deploy time
gcloud secrets add-iam-policy-binding sargapp-supabase-url \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/secretmanager.secretAccessor" \
  --project="$PROJECT_ID" --quiet 2>/dev/null || true
# (Repeat for each secret — Cloud Run also reads them at container startup
#  via the Cloud Run service identity, granted automatically by --secrets flag)

# ── 4. Initial placeholder Cloud Run deploys (required before LB can use them)
echo ""
echo "▶ Creating placeholder Cloud Run services…"
gcloud run deploy sargapp-api \
  --image="gcr.io/cloudrun/hello" \
  --region="$REGION" \
  --allow-unauthenticated \
  --port=8080 \
  --project="$PROJECT_ID" \
  --quiet 2>/dev/null || echo "  API service already exists."

gcloud run deploy sargapp-dashboard \
  --image="gcr.io/cloudrun/hello" \
  --region="$REGION" \
  --allow-unauthenticated \
  --port=8080 \
  --project="$PROJECT_ID" \
  --quiet 2>/dev/null || echo "  Dashboard service already exists."

# ── 5. Load Balancer + custom domain ─────────────────────────────────────────
echo ""
echo "▶ Reserving static IP…"
gcloud compute addresses create sargapp-lb-ip \
  --global --project="$PROJECT_ID" 2>/dev/null || echo "  IP already exists."
LB_IP=$(gcloud compute addresses describe sargapp-lb-ip \
  --global --project="$PROJECT_ID" --format="value(address)")
echo "  ✅ IP: $LB_IP"

echo ""
echo "▶ Creating Serverless NEGs…"
for SVC in api dashboard; do
  gcloud compute network-endpoint-groups create "sargapp-${SVC}-neg" \
    --region="$REGION" \
    --network-endpoint-type=SERVERLESS \
    --cloud-run-service="sargapp-${SVC}" \
    --project="$PROJECT_ID" 2>/dev/null || echo "  $SVC NEG already exists."
done

echo ""
echo "▶ Creating backend services…"
for SVC in api dashboard; do
  gcloud compute backend-services create "sargapp-${SVC}-backend" \
    --global --load-balancing-scheme=EXTERNAL_MANAGED \
    --project="$PROJECT_ID" 2>/dev/null || echo "  $SVC backend already exists."
  gcloud compute backend-services add-backend "sargapp-${SVC}-backend" \
    --global \
    --network-endpoint-group="sargapp-${SVC}-neg" \
    --network-endpoint-group-region="$REGION" \
    --project="$PROJECT_ID" 2>/dev/null || true
done

echo ""
echo "▶ Creating URL map (host-based routing)…"
cat > /tmp/sargapp-url-map.yaml << YAML
defaultService: global/backendServices/sargapp-dashboard-backend
hostRules:
  - hosts: ["${DOMAIN}", "www.${DOMAIN}"]
    pathMatcher: dashboard
  - hosts: ["api.${DOMAIN}"]
    pathMatcher: api
pathMatchers:
  - name: dashboard
    defaultService: global/backendServices/sargapp-dashboard-backend
  - name: api
    defaultService: global/backendServices/sargapp-api-backend
YAML
gcloud compute url-maps import sargapp-url-map \
  --global --source=/tmp/sargapp-url-map.yaml \
  --project="$PROJECT_ID" --quiet 2>/dev/null || echo "  URL map already exists."

echo ""
echo "▶ Creating Google-managed SSL certificate…"
gcloud compute ssl-certificates create sargapp-cert \
  --domains="${DOMAIN},www.${DOMAIN},api.${DOMAIN}" \
  --global --project="$PROJECT_ID" 2>/dev/null || echo "  Cert already exists."

echo ""
echo "▶ Creating HTTPS proxy and forwarding rules…"
gcloud compute target-https-proxies create sargapp-https-proxy \
  --url-map=sargapp-url-map \
  --ssl-certificates=sargapp-cert \
  --global --project="$PROJECT_ID" 2>/dev/null || echo "  HTTPS proxy exists."
gcloud compute forwarding-rules create sargapp-https-fwd \
  --global --target-https-proxy=sargapp-https-proxy \
  --address=sargapp-lb-ip --ports=443 \
  --load-balancing-scheme=EXTERNAL_MANAGED \
  --project="$PROJECT_ID" 2>/dev/null || echo "  HTTPS fwd rule exists."

# HTTP → HTTPS redirect
gcloud compute url-maps import sargapp-http-redirect \
  --global --project="$PROJECT_ID" \
  --source=<(printf 'defaultUrlRedirect:\n  httpsRedirect: true\n  redirectResponseCode: MOVED_PERMANENTLY_DEFAULT\n') \
  2>/dev/null || true
gcloud compute target-http-proxies create sargapp-http-proxy \
  --url-map=sargapp-http-redirect --global \
  --project="$PROJECT_ID" 2>/dev/null || true
gcloud compute forwarding-rules create sargapp-http-fwd \
  --global --target-http-proxy=sargapp-http-proxy \
  --address=sargapp-lb-ip --ports=80 \
  --load-balancing-scheme=EXTERNAL_MANAGED \
  --project="$PROJECT_ID" 2>/dev/null || true

# ── 6. Print GitHub Secrets to add ────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════"
echo "✅ GCP setup complete!"
echo ""
echo "ACTION 1 — Add these DNS A records at your registrar:"
echo "  ${DOMAIN}         A  ${LB_IP}"
echo "  www.${DOMAIN}     A  ${LB_IP}"
echo "  api.${DOMAIN}     A  ${LB_IP}"
echo ""
echo "ACTION 2 — Add these GitHub Secrets"
echo "  (Settings → Secrets and variables → Actions → New repository secret):"
echo ""
echo "  GCP_PROJECT_ID    = ${PROJECT_ID}"
echo "  GCP_REGION        = ${REGION}"
echo "  GCP_SA_KEY        = (contents of ${KEY_FILE})"
echo "  SUPABASE_URL      = (your Supabase URL — already in Secret Manager)"
echo "  SUPABASE_KEY      = (your Supabase key — already in Secret Manager)"
echo "  TELEGRAM_BOT_TOKEN= (already in Secret Manager)"
echo "  API_BASE_URL      = https://api.${DOMAIN}"
echo "  CUSTOM_DOMAIN     = ${DOMAIN}"
echo ""
echo "ACTION 3 — Push to main branch to trigger first deploy."
echo ""
echo "  SSL cert takes 10–30 min to provision after DNS propagates."
echo "  Check: gcloud compute ssl-certificates describe sargapp-cert --global"
echo "════════════════════════════════════════════"
