# Sargassum POC — The Build Prompt

A complete, paste-ready prompt package for coding the POC with an AI assistant. Two ways to use it:

* **Autonomous agent (Claude Code / Cursor agent):** save Part A as `CLAUDE.md` (or `.cursorrules`) in an empty repo so it's always in context, then paste the Part B phase prompts one at a time.
* **Chat (Claude / ChatGPT):** paste Part A once to set context, then paste each Part B phase prompt in order. Test after each before moving on.

> **Golden rule:** one phase per message. Don't paste the whole thing at once — small verifiable steps are what make AI builds succeed.

---

# PART A — Standing Project Brief (paste first / save as `CLAUDE.md`)

```
ROLE
You are my pair-programmer building a proof-of-concept "Sargassum Detection &
Early Warning System" for the Dominican Republic. We build it in small,
testable steps. After each piece you give me an exact local test command and
wait for me to confirm it passes before continuing.

OBJECTIVE
Detect floating sargassum from satellite imagery over the DR Exclusive Economic
Zone, model its drift using ocean currents + wind, compute an arrival ETA for a
handful of coastal zones, store the results, and push lightweight alerts to
fishermen and hotels via Telegram. Keep it runnable end-to-end on free tiers.

HARD CONSTRAINTS
1. Free-tier only. Target ~$0/month. No paid services in the POC.
2. All secrets come from environment variables. NEVER hardcode a key, token,
   password, or connection string. Maintain a .env.example with every variable.
3. Keep it SIMPLE. Single satellite sensor + spectral-index threshold for
   detection (no ML yet). Basic Lagrangian advection for drift (no physics
   solver yet). We can upgrade later; the POC proves the pipeline and alert loop.
4. Telegram first. Do NOT build WhatsApp — it needs Meta approval.
5. Small, well-named modules following the repo layout below. No giant scripts.
6. Before writing any geospatial code (Earth Engine, Copernicus), briefly explain
   your approach and the exact dataset/product you'll use, then write the code.
7. If a library method might be outdated, flag it and tell me what to verify in
   the official docs rather than guessing. These APIs change.
8. Add structured logging and graceful error handling so one failed step doesn't
   crash the whole pipeline.

TECH STACK (pin these)
- Python 3.11
- Detection: earthengine-api, geemap, geopandas, shapely, numpy
- Ocean data: copernicusmarine
- Database: Supabase (Postgres + PostGIS) via supabase-py
- API: FastAPI + uvicorn + pydantic
- Bot: python-telegram-bot (or httpx for raw Bot API calls)
- Dashboard: streamlit + folium/pydeck
- Scheduling: GitHub Actions (cron)
- Hosting: Render (API, free web service), Streamlit Community Cloud (dashboard)
- Config: python-dotenv

REPO LAYOUT (create exactly this)
sargassum-poc/
├── .github/workflows/
│   ├── pipeline.yml        # cron: run the pipeline every 6h
│   └── keepalive.yml       # daily ping to /health (prevents Supabase idle-pause)
├── pipeline/
│   ├── __init__.py
│   ├── config.py           # EEZ bbox, zones, thresholds, wind-drift factor (from env)
│   ├── detect.py           # GEE: fetch + cloud mask + FAI/NDVI -> patch polygons
│   ├── ocean.py            # Copernicus: currents + wind at patch points
│   ├── drift.py            # Lagrangian advection -> ETA & risk per zone
│   ├── store.py            # upsert detections + forecasts to Supabase
│   ├── dispatch.py         # match forecasts to subscribers, de-dupe, send alerts
│   └── run.py              # orchestrate steps 1-6
├── api/
│   ├── __init__.py
│   ├── main.py             # FastAPI app + routes
│   ├── models.py           # Pydantic schemas
│   ├── db.py               # Supabase client + queries
│   └── telegram.py         # bot webhook + command handlers + send helper
├── dashboard/
│   └── app.py              # Streamlit risk map
├── sql/
│   └── schema.sql          # tables + PostGIS + seed zones
├── tests/
├── requirements.txt
├── render.yaml
├── .env.example
└── README.md

DATA CONTRACTS (use these exact shapes)

Environment variables (.env.example):
  EE_PROJECT=                 # Google Cloud project id with Earth Engine enabled
  EE_SERVICE_ACCOUNT_JSON=    # path to service-account key OR the JSON string
  CMEMS_USERNAME=
  CMEMS_PASSWORD=
  SUPABASE_URL=
  SUPABASE_KEY=               # service role key for the pipeline; anon key for read API
  TELEGRAM_BOT_TOKEN=
  API_BASE_URL=               # public Render URL, used by dashboard + webhook registration

Database tables:
  zones(id PK, name, geom geometry(POLYGON,4326), center_lat, center_lon)
  detections(id PK, run_at timestamptz, geom geometry(POLYGON,4326),
             centroid geometry(POINT,4326), area_km2, source)
  forecasts(id PK, run_at timestamptz, zone_id FK->zones, risk_level
            text CHECK in ('none','low','medium','high'), eta_hours int,
            eta_timestamp timestamptz)
  subscribers(id PK, channel text default 'telegram', chat_id text, zone_id
              FK->zones, role text, last_alerted timestamptz, created_at)

API endpoints (lightweight JSON):
  GET  /health                  -> {status, db: ok} (also used as keep-alive)
  GET  /zones                   -> [{id, name, center_lat, center_lon}]
  GET  /forecast                -> [{zone_id, name, risk_level, eta_hours, eta_timestamp, run_at}]
  GET  /forecast/{zone_id}      -> latest forecast for one zone
  POST /subscribe               -> body {channel, chat_id, zone_id, role}; inserts subscriber
  POST /telegram/webhook        -> Telegram update handler

Five seed zones (name, approx center lat, lon):
  Punta Cana     18.58, -68.37
  Bavaro         18.68, -68.43
  Samana         19.20, -69.33
  Puerto Plata   19.80, -70.69
  Juan Dolio     18.43, -69.42
(Build each zone polygon as a small box around its center; I can refine later.)

DR EEZ bounding box (coarse, refine later with a real EEZ GeoJSON):
  lat 17.3 .. 21.5, lon -72.1 .. -67.3

Alert message format (plain text, low-bandwidth, Spanish-friendly):
  "ALERTA SARGAZO — {zone}: riesgo {RISK}. Llegada estimada ~{eta_hours}h
   ({eta_local}). Planifica con tiempo."

Risk thresholds (tune in config.py):
  none   : no patch projected into the zone
  low    : projected patch total area < 1 km2
  medium : 1–10 km2
  high   : > 10 km2 OR eta_hours <= 24

DEFINITION OF DONE
- `python -m pipeline.run` completes end-to-end and populates Supabase.
- GitHub Actions runs the pipeline on a cron, unattended.
- FastAPI on Render serves the endpoints above.
- A Telegram bot supports /start, /subscribe <zone>, /status, /stop.
- High-risk forecasts auto-send a de-duplicated Telegram alert.
- A Streamlit map shows current zone risk.
- No secrets in code; .env.example complete; README explains setup.

WORKING STYLE
- Propose the approach, then code, then give me the test command.
- Prefer standard library + the pinned deps; ask before adding new dependencies.
- Commit-sized chunks. Tell me a good commit message after each green step.
```

---

# PART B — Phase Prompts (paste one at a time, in order)

### Phase 0 — Scaffold

```
Scaffold the repo exactly as in the project brief: all directories, __init__.py
files, and stub modules each with a one-line docstring describing its purpose.
Generate requirements.txt with the pinned stack, a complete .env.example with
every variable from the brief, a .gitignore (ignore .env, __pycache__, *.pyc,
.venv), and a README with setup steps. Do NOT implement logic yet. Then give me
the git commands to initialise the repo and make the first commit.
```

### Phase 1 — Accounts & credentials (guided, no app code)

```
Walk me through, step by step, how to obtain every credential in .env.example:
(1) create a Google Cloud project, enable Earth Engine, register for
noncommercial access, and create a service account key for unattended use;
(2) register for Copernicus Marine and get username/password;
(3) create a Supabase project and find the URL + service-role and anon keys;
(4) create a Telegram bot with BotFather and get the token.
For each, tell me the exact value to copy and which .env variable it goes in.
Then give me a tiny check_env.py that loads .env and prints which variables are
set (without printing the values) so I can confirm my setup.
```

### Phase 2 — Detection

```
Implement pipeline/detect.py per the brief. First explain the FAI band math for
Sentinel-2 and why we cloud-mask. Then write detect_sargassum(date_range,
eez_geojson) using the Earth Engine Python API: load Sentinel-2 SR for the DR
EEZ, cloud-mask, compute FAI and NDVI, threshold to isolate floating algae, and
return a GeoDataFrame of patch polygons with centroid lat/lon and area_km2. Put
all thresholds and the EEZ bbox in config.py. Give me a standalone test that
runs it for the last 7 days, prints the patch count, and writes patches.geojson
so I can eyeball it on geojson.io. If detection looks noisy, tell me how to add
a minimum-area filter and a coastal/depth mask.
```

### Phase 3 — Ocean forcing

```
Implement pipeline/ocean.py: get_ocean_forcing(points, forecast_hours=72) using
the copernicusmarine toolbox to fetch surface current (u,v) and wind vectors for
a list of lat/lon points over the next 72h. First tell me which CMEMS product
you're using and why. Return a tidy structure keyed by point that drift.py can
consume. Give me a test that fetches forcing for one point off Punta Cana and
prints the vectors with units. Flag anything in the copernicusmarine API you're
unsure is current and tell me what to verify.
```

### Phase 4 — Drift model + ETA

```
Implement pipeline/drift.py: project_drift(patches, forcing, hours=[24,48,72]).
Use Lagrangian advection — move each patch centroid forward in timesteps using
current velocity plus a configurable wind-drift factor (default 1.5% of wind
speed). For each of the 5 zones in config.py, decide whether any projected patch
enters the zone and at what hour (the ETA), then assign a risk_level using the
brief's thresholds. Return a list of zone forecasts {zone_id, risk_level,
eta_hours, eta_timestamp}. Explain the wind-drift factor choice. Include a test
with SYNTHETIC patches and forcing so it runs offline and asserts a known patch
reaches a known zone.
```

### Phase 5 — Database

```
Write sql/schema.sql for Supabase: enable PostGIS, create the four tables from
the brief with the exact columns and constraints, and seed the 5 zones as small
boxes around their centers. Then implement pipeline/store.py with
upsert_detections(gdf) and upsert_forecasts(list) using supabase-py. Tell me how
to run the schema in the Supabase SQL editor, and give me a test that inserts
one synthetic detection + forecast and reads it back.
```

### Phase 6 — Orchestrate

```
Implement pipeline/run.py: a main() that runs detect -> ocean -> drift -> store
in order, with structured logging of each step's result and duration, made
idempotent and wrapped so a single step failure is logged but handled. Give me
the one command to run the full pipeline locally and tell me what rows I should
expect to see in Supabase afterward.
```

### Phase 7 — Schedule on GitHub Actions

```
Write .github/workflows/pipeline.yml: run `python -m pipeline.run` on cron every
6 hours and on manual dispatch. Install deps, set up Earth Engine
service-account auth from a GitHub Secret (interactive auth won't work in CI),
and pass all credentials from GitHub Secrets as env vars. List every secret I
must add in repo settings, and tell me how to trigger a manual run to test it.
```

### Phase 8 — Read API

```
Implement api/main.py, api/models.py, api/db.py per the brief's endpoint list.
Lightweight JSON responses, Pydantic validation, Supabase reads/writes, and a
/health that also pings the DB. Give me uvicorn run instructions and an example
curl for every endpoint.
```

### Phase 9 — Telegram bot

```
Implement api/telegram.py and wire POST /telegram/webhook into the FastAPI app.
Handle /start, /subscribe <zone> (register chat_id + zone as a subscriber via
the API/db), /status (reply with the latest forecast for the user's zone),
/stop. Add send_alert(chat_id, message). Keep replies short and plain-text in
the brief's format. Tell me how to register the webhook once deployed, and give
me a local test path (ngrok or a polling fallback).
```

### Phase 10 — Close the alert loop

```
Implement pipeline/dispatch.py and call it as step 6 in run.py: after forecasts
are written, find subscribers whose zone has risk medium/high with ETA <= 72h,
send each a Telegram alert, and update last_alerted so nobody is spammed for the
same event. Give me a test that simulates a high-risk forecast and asserts an
alert would be sent exactly once.
```

### Phase 11 — Dashboard

```
Implement dashboard/app.py in Streamlit: a folium/pydeck map of the DR coast
showing the 5 zones colored by current risk and the latest detected patches,
plus a sidebar listing each zone's ETA. Pull data from the API endpoints using
API_BASE_URL. Tell me how to deploy it free on Streamlit Community Cloud.
```

### Phase 12 — Deploy

```
Write render.yaml for a free Render web service running the FastAPI app with
uvicorn, listing the env vars to set in the Render dashboard. Write
.github/workflows/keepalive.yml that curls /health once a day to prevent
Supabase idle-pause. Walk me through: connect the repo to Render, deploy, get my
public URL, set API_BASE_URL everywhere it's needed, and register the Telegram
webhook against the Render URL. Finish with a checklist mapping each Definition
of Done item to how I verify it.
```

---

# PART C — Prompts you'll reuse while building

```
Here's the full traceback: [paste]. Explain the root cause and give me the
minimal fix — don't refactor unrelated code.
```

```
This Earth Engine call returns an empty collection. Walk me through debugging it
step by step (date range, region, band names, cloud mask), one check at a time.
```

```
Is this copernicusmarine / earthengine-api / python-telegram-bot method current
as of today? If you're not sure, tell me exactly what to check in the official
docs.
```

```
My GitHub Actions run fails at the Earth Engine auth step: [paste log]. Fix the
service-account setup for CI.
```

```
Refactor this into the module structure from the project brief, keeping behavior
identical, and give me the test commands again.
```

---

## How to drive it well

1. Keep Part A in context the whole time (a `CLAUDE.md` / `.cursorrules` file is ideal — agents auto-load it).
2. Run Phase 2 (detection) on a day you have time — it's the hardest and most API-sensitive.
3. After every green test, commit. Working states you can return to are worth more than speed.
4. The moment a phase touches credentials, double-check nothing landed in a tracked file: `git diff --staged` before committing.
5. When the agent says "this API might have changed," believe it — open the doc and confirm rather than letting it guess.

```

```
