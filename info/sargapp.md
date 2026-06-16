# Sargassum POC — Vibe-Coding Build Playbook (≈ $0/month)

A step-by-step guide to building a cheap, scalable proof-of-concept using AI coding assistants (Claude, Cursor, Claude Code, etc.). Each milestone has a **copy-paste prompt** you can hand to your AI assistant, plus what to check and how to test.

> **How to use this doc** : Work milestone by milestone. Paste the prompt block into your AI coding tool, review what it generates, test it, commit, then move on. Don't try to build everything in one prompt — small, verifiable steps win.

---

## 0. The Free-Tier Stack (verified June 2026)

| Layer             | Service                                            | Free tier reality                                                                           | Caveat                                                             |
| ----------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Satellite imagery | **Google Earth Engine**                      | Free for noncommercial/research; monthly EECU compute quota (Community Tier) since Apr 2026 | A*commercial*launch needs a paid plan — POC as research is fine |
| Ocean data        | **Copernicus Marine (CMEMS)**                | Free with registration                                                                      | Requires account + API credentials                                 |
| Scheduled compute | **GitHub Actions**                           | Free (2000 min/mo private, unlimited public)                                                | The cron that runs your whole pipeline                             |
| Database          | **Supabase**(Postgres + PostGIS)             | 500MB DB, free                                                                              | Pauses after 7 days idle → add a keep-alive ping                  |
| API host          | **Render**free web service                   | Free, real HTTPS URL                                                                        | Spins down after 15 min → 30–50s cold start (OK for POC)         |
| Alerts            | **Telegram Bot API**                         | 100% free, instant                                                                          | Start here. WhatsApp Business API needs Meta approval — add later |
| Dashboard         | **Streamlit Community Cloud**                | Free hosting for one app                                                                    | Or static map on Vercel/Netlify                                    |
| Secrets           | **GitHub Secrets**+**Render env vars** | Free                                                                                        | Never commit keys                                                  |

**Total: $0/month** for a working POC. First paid upgrade you'll likely want is Render Starter ($7/mo) to kill cold starts, or Supabase Pro ($25/mo) to stop the idle-pause — neither needed to prove the concept.

**Why this differs from the production design:** the production doc proposed multi-sensor fusion, deep-learning detection, and physics-informed drift. The POC deliberately uses the *simplest thing that works end-to-end* (single sensor, index threshold, basic advection) so you can prove the pipeline and the alert loop before investing in ML.

---

## 1. Skills Matrix — What You Need vs. What the AI Handles

You do **not** need to be an expert in all of these. The point of vibe-coding is the AI writes the code; you need enough literacy to steer, test, and debug.

### Skills you should have (the steering wheel)

* **Basic Python literacy** — read a function, run a script, understand an error message
* **Git basics** — clone, commit, push, branches (the AI can guide you)
* **Reading JSON / understanding APIs** — what an endpoint is, what a payload looks like
* **Geospatial intuition** — lat/lon, what a polygon is, what "the EEZ" means
* **Willingness to read docs** — when the AI is unsure about a current API, you verify

### Skills the AI handles for you (just describe what you want)

* Earth Engine Python API syntax (it's verbose; let the AI write it)
* FastAPI route scaffolding, Pydantic models, async patterns
* SQL schema + PostGIS spatial queries
* Telegram bot webhook handling
* GitHub Actions YAML
* Dockerfiles, deploy configs, requirements.txt

### Concepts worth a 20-minute skim before you start

1. **Floating Algae Index (FAI)** — the spectral formula that makes sargassum "light up." Ask your AI: *"Explain FAI and NDVI for sargassum detection in plain terms, with the band math for Sentinel-2."*
2. **Lagrangian advection** — moving a particle along current + wind vectors over time. This is your drift model in one sentence.
3. **Webhooks vs. polling** — how Telegram tells your API a message arrived.
4. **Cron syntax** — `0 */6 * * *` = every 6 hours.

---

## 2. Vibe-Coding Ground Rules (read once, saves hours)

These are the habits that make AI-assisted builds succeed instead of producing a tangle:

1. **One milestone per conversation/session.** Don't ask for the whole app at once. Build the pipeline, get it working, *then* build the API.
2. **Always ask for tests or a manual test step.** End prompts with *"...and tell me exactly how to run and verify this locally."*
3. **Make the AI explain before it builds** for anything geospatial: *"Before writing code, explain your approach and the data sources you'll use."* This catches wrong assumptions about current APIs.
4. **Force current-API verification.** Earth Engine, Copernicus, and Telegram APIs change. Add: *"If you're unsure whether this API/method is current, say so and tell me what to check in the official docs."*
5. **Keep secrets out of code from message one.** Tell the AI: *"Use environment variables for all credentials; never hardcode keys."*
6. **Commit after every green test.** Working state you can return to.
7. **Small files, clear names.** Ask for `pipeline/detect.py`, `pipeline/drift.py`, `api/main.py` — not one giant script.
8. **When stuck, paste the full error.** The whole traceback, not a paraphrase.

### A reusable system-prompt preamble

Paste this at the start of any coding session:

> You are helping me build a proof-of-concept sargassum detection and alert system on free-tier services. Stack: Python, Google Earth Engine, Copernicus Marine, Supabase Postgres+PostGIS, FastAPI on Render, Telegram bot, GitHub Actions for scheduling. Priorities: keep it simple, free, and testable. Use environment variables for all secrets. Before writing geospatial code, briefly explain your approach. If any API method might be outdated, flag it and tell me what to verify. Give me a runnable local test step after each piece.

---

## 3. Repository Layout (ask the AI to scaffold this first)

```
sargassum-poc/
├── .github/workflows/
│   └── pipeline.yml          # cron: runs the pipeline every 6h
├── pipeline/
│   ├── __init__.py
│   ├── config.py             # zones, EEZ bbox, thresholds (from env)
│   ├── detect.py             # GEE: fetch + FAI/NDVI + extract polygons
│   ├── ocean.py              # Copernicus: currents + wind
│   ├── drift.py              # advection model → ETA per zone
│   ├── store.py              # write to Supabase
│   └── run.py                # orchestrates steps 1–6
├── api/
│   ├── main.py               # FastAPI app
│   ├── models.py             # Pydantic schemas
│   ├── db.py                 # Supabase client
│   └── telegram.py           # bot webhook + send helpers
├── dashboard/
│   └── app.py                # Streamlit map
├── sql/
│   └── schema.sql            # tables + PostGIS
├── tests/
├── requirements.txt
├── render.yaml               # Render deploy config
├── .env.example
└── README.md
```

**Milestone 0 prompt:**

> Scaffold a Python project with this exact directory structure: [paste the tree above]. Create empty/stub files with a one-line docstring describing each module's purpose, a `requirements.txt` with the libraries we'll need (earthengine-api, geemap, copernicusmarine, geopandas, shapely, numpy, fastapi, uvicorn, supabase, python-telegram-bot or httpx, streamlit, python-dotenv), a `.env.example` listing every secret as a placeholder, and a README explaining setup. Don't implement logic yet — just the skeleton I can commit.

---

## 4. Build Milestones (with prompts)

### Milestone 1 — Accounts & credentials (no code)

Before coding, create free accounts and collect credentials. Ask your AI to walk you through each:

> Walk me through, step by step, how to: (1) create a Google Cloud project and register it for Earth Engine noncommercial access, then authenticate the Python API; (2) register for a Copernicus Marine account and get API credentials; (3) create a Supabase project and find my connection string + anon key; (4) create a Telegram bot with BotFather and get the token. For each, tell me exactly which value to copy and which env var name to store it in.

**Checkpoint:** You have a `.env` file (git-ignored) with: `EE_PROJECT`, `EE_SERVICE_ACCOUNT_JSON`, `CMEMS_USERNAME`, `CMEMS_PASSWORD`, `SUPABASE_URL`, `SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN`.

> **Earth Engine auth tip for automation:** interactive `ee.Authenticate()` won't work in GitHub Actions. Ask the AI: *"Set up Earth Engine authentication using a service account so it runs unattended in GitHub Actions."*

---

### Milestone 2 — Detection (the heart of it)

> In `pipeline/detect.py`, write a function `detect_sargassum(date_range, eez_geojson)` using the Earth Engine Python API that: loads Sentinel-2 surface reflectance for the Dominican Republic EEZ, applies a cloud mask, computes the Floating Algae Index and NDVI, thresholds them to isolate floating algae, and returns the detected patches as a GeoDataFrame of polygons with centroid lat/lon and area. Keep thresholds in `config.py` so I can tune them. Before coding, explain the FAI band math for Sentinel-2 and why we mask clouds. Then give me a standalone test that runs it for the last 7 days and prints how many patches it found.

**Checkpoint:** Running the test prints a patch count and you can eyeball a few centroids on a map (ask for a `geemap` preview or export GeoJSON to paste into geojson.io).

**If detection is noisy:** ask *"My FAI threshold is catching clouds/shallow reefs. Suggest 3 ways to reduce false positives without a heavy ML model."* (Typical answers: stricter threshold, a coastal/depth mask, a minimum-area filter.)

---

### Milestone 3 — Ocean forcing

> In `pipeline/ocean.py`, write `get_ocean_forcing(points, forecast_hours)` that uses the Copernicus Marine toolbox to fetch surface current (u, v) and wind vectors for a list of lat/lon points over the next 72 hours, returning a tidy structure I can feed into a drift model. Explain which CMEMS product/dataset you're using and why. Give me a test that fetches forcing for one point off Punta Cana and prints the vectors.

**Checkpoint:** You get plausible current/wind vectors (m/s) for a test point.

---

### Milestone 4 — Drift model + ETA

> In `pipeline/drift.py`, implement a simple Lagrangian advection model: `project_drift(patches, ocean_forcing, hours=[24,48,72])`. Move each patch centroid forward in timesteps using current velocity plus a wind-drift factor (start with 1–2% of wind speed — make it configurable). For each coastal zone defined in `config.py` (give me 5 example DR zones with bounding boxes: e.g. Punta Cana, Samaná, Puerto Plata, Juan Dolio, La Romana), determine whether any projected patch enters the zone and at what hour — that's the ETA. Return a list of zone forecasts with risk level (none/low/med/high based on patch area) and ETA. Explain the wind-drift factor choice. Add a test with synthetic patches and forcing so it runs without hitting APIs.

**Checkpoint:** Synthetic test produces zone forecasts with ETAs. This is your core value — a patch in open water maps to "arrives at Zone X in ~Y hours."

---

### Milestone 5 — Database schema + storage

> Write `sql/schema.sql` for Supabase Postgres with PostGIS enabled. Tables: `detections` (id, run_at, geom POLYGON, centroid POINT, area_km2, source), `forecasts` (id, run_at, zone_id, risk_level, eta_hours, eta_timestamp), `zones` (id, name, geom, center_lat, center_lon), `subscribers` (id, channel, chat_id, zone_id, role, created_at). Then write `pipeline/store.py` with functions to upsert detections and forecasts using the supabase-py client. Give me the SQL to seed the 5 example zones. Tell me how to run the schema in the Supabase SQL editor and how to test the upsert from Python.

**Checkpoint:** You run the pipeline once and see rows appear in the Supabase table editor.

---

### Milestone 6 — Orchestrate the pipeline

> In `pipeline/run.py`, wire steps 1–6 into one `main()`: detect → fetch ocean forcing for patch locations → drift/ETA → store detections + forecasts → return a summary. Add structured logging so each step prints what it did and how long it took. Make it idempotent (safe to re-run). Give me the single command to run the whole pipeline locally.

**Checkpoint:** `python -m pipeline.run` completes end to end and populates the database. **This is the POC's spine — once this works, you've proven the concept.**

---

### Milestone 7 — Schedule it for free (GitHub Actions)

> Write `.github/workflows/pipeline.yml` that runs `python -m pipeline.run` on a cron every 6 hours and on manual dispatch. Install dependencies, set up Earth Engine service-account auth from a GitHub Secret, and pass all credentials from GitHub Secrets as env vars. Tell me exactly which secrets to add in the repo settings and how to trigger a manual run to test it.

**Checkpoint:** A manual Actions run goes green and you see fresh rows in Supabase. Your pipeline now runs unattended, for free.

---

### Milestone 8 — FastAPI read API

> Build `api/main.py` (FastAPI) with: `GET /zones` (list zones), `GET /forecast/{zone_id}` (latest forecast for a zone), `GET /forecast` (all current zone forecasts), `POST /subscribe` (body: channel, chat_id, zone_id, role — insert a subscriber), and `GET /health`. Use `api/db.py` for the Supabase client and `api/models.py` for Pydantic schemas. Return clean, lightweight JSON. Add a `/health` that also pings the DB so I can use it as a keep-alive. Give me uvicorn run instructions and example curl commands for each endpoint.

**Checkpoint:** `curl localhost:8000/forecast` returns the zone forecasts your pipeline wrote.

---

### Milestone 9 — Telegram bot

> In `api/telegram.py`, add a Telegram bot integrated into the FastAPI app: a webhook endpoint `POST /telegram/webhook` that handles `/start`, `/subscribe <zone>` (registers the chat_id + zone as a subscriber), `/status` (replies with the latest forecast for their subscribed zone), and `/stop`. Also add a `send_alert(chat_id, message)` helper. Explain how to register the webhook URL with Telegram once I'm deployed. Keep messages short and plain-text for low bandwidth. Give me a way to test the bot logic locally with ngrok or a polling fallback.

**Checkpoint:** You message your bot `/status` and it replies with a forecast.

---

### Milestone 10 — Close the alert loop

> Add `dispatch_alerts()` to the pipeline (call it as step 6 in `run.py`): after writing forecasts, find subscribers whose zone has risk >= medium with an ETA inside 72h, and send each a Telegram alert via the bot token. De-duplicate so the same person isn't alerted repeatedly for the same event (track a `last_alerted` per subscriber+zone). Give me a test that simulates a high-risk forecast and confirms an alert would be sent.

**Checkpoint:** A simulated high-risk run sends you a real Telegram alert. **The full loop now works: satellite → detection → drift → your phone.**

---

### Milestone 11 — Dashboard (optional but great for demos)

> Build `dashboard/app.py` in Streamlit: a map (folium/pydeck) of the DR coast showing the 5 zones colored by current risk level, latest detected patches as polygons, and a sidebar listing each zone's ETA. Pull data from the FastAPI endpoints. Tell me how to deploy it free on Streamlit Community Cloud.

**Checkpoint:** A shareable URL showing a live risk map — perfect for pitching to stakeholders or funders.

---

## 5. Deployment (all free)

### Deploy the API to Render

> Write a `render.yaml` for a free Render web service running the FastAPI app with uvicorn. List the env vars I need to set in the Render dashboard (Supabase + Telegram). Walk me through connecting my GitHub repo, deploying, and getting my public HTTPS URL. Then tell me the exact command/URL to register that URL as my Telegram webhook.

**Deploy order that avoids circular dependencies:**

1. Schema → Supabase (Milestone 5)
2. Pipeline → GitHub Actions, run once manually (Milestone 7)
3. API → Render, get public URL (this step)
4. Register Telegram webhook against the Render URL
5. Dashboard → Streamlit Cloud (Milestone 11)

### Keep the free tiers alive

* **Supabase idle-pause:** add a GitHub Actions cron (or cron-job.org) that hits `GET /health` daily.
* **Render cold start:** acceptable for a POC. If a demo needs instant response, hit `/health` a minute before, or upgrade to Starter ($7/mo) later.

**Keep-alive prompt:**

> Add a second tiny GitHub Actions workflow that curls my Render `/health` endpoint once a day to keep Supabase from idle-pausing. Free-tier friendly.

---

## 6. Scaling Path (when the POC proves out)

Stay free as long as possible; upgrade only what hurts:

| Symptom                    | Cheap fix                             | Real fix                                             |
| -------------------------- | ------------------------------------- | ---------------------------------------------------- |
| Cold-start delays in demos | daily keep-alive ping                 | Render Starter $7/mo                                 |
| DB idle-pause              | health-check cron                     | Supabase Pro $25/mo                                  |
| Detection too noisy/coarse | tune thresholds, add masks            | swap in the ML model from the production doc (U-Net) |
| Need cloud-free detection  | add Sentinel-3 daily                  | add Sentinel-1 SAR                                   |
| WhatsApp required          | —                                    | apply for WhatsApp Business API                      |
| More zones / users         | partition by zone                     | move pipeline to Cloud Run, add caching              |
| EE compute quota hit       | optimize EECU usage, reduce frequency | EE commercial plan (required once you charge users)  |

**Important licensing note:** Earth Engine's free tier is  *noncommercial only* . The moment this serves a paying customer or fulfills a paid deliverable, you need a commercial EE plan. Validate the concept on the free tier; budget for EE commercial in any real funding ask.

---

## 7. One-Week Build Sprint (suggested order)

* **Day 1:** Milestones 0–1 (scaffold + accounts/credentials)
* **Day 2:** Milestone 2 (detection) — the hardest, give it time
* **Day 3:** Milestones 3–4 (ocean + drift/ETA)
* **Day 4:** Milestones 5–6 (DB + orchestrate the full pipeline)
* **Day 5:** Milestone 7 (schedule on GitHub Actions)
* **Day 6:** Milestones 8–10 (API + Telegram + alert loop)
* **Day 7:** Milestone 11 + deploy (dashboard + Render + webhook)

End of week: a free, scheduled, end-to-end POC that detects sargassum, predicts arrival, and texts fishermen.

---

## 8. Debugging Prompts You'll Reuse

* *"Here's the full traceback: [paste]. Explain the root cause and give me the minimal fix."*
* *"This Earth Engine call returns an empty collection. Walk me through how to debug it step by step."*
* *"My GitHub Actions run fails at the auth step: [paste log]. Fix the service-account setup."*
* *"Is this Copernicus Marine method current as of now? If unsure, tell me what to check in their docs."*
* *"Refactor this 200-line script into the module structure from my repo layout."*
* *"Add input validation and graceful error handling so one failed step doesn't crash the whole pipeline."*

---

## 9. What "Done" Looks Like for the POC

✅ A GitHub Actions cron runs the pipeline every 6h, unattended, free
✅ Each run detects sargassum patches from Sentinel imagery over the DR EEZ
✅ A drift model projects arrival and computes an ETA for 5 coastal zones
✅ Forecasts persist in Supabase
✅ A FastAPI service on Render serves zone forecasts as JSON
✅ A Telegram bot lets users subscribe to a zone and get `/status`
✅ High-risk forecasts auto-dispatch Telegram alerts
✅ A Streamlit map shows live risk for demos
✅ Total recurring cost: **$0**

That's a fundable, demonstrable proof of concept you can put in front of fishing cooperatives, hotel associations, and grant committees — before spending a peso on ML or commercial data.
