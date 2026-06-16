# 🌊 Sargassum Detection & Early-Warning System

> **POC** · Dominican Republic EEZ · Free-tier · ~$0/month

Detects floating sargassum from satellite imagery, models its drift with ocean currents and wind, computes an arrival ETA for DR coastal zones, and sends Telegram alerts to fishermen and hotels.
Also ships a tourist **beach explorer** — 56 DR beaches with live sargassum risk, activities, wildlife, parking, and Google Maps links.

---

## Quick start (5 minutes to a running dashboard)

You don't need Earth Engine or Copernicus credentials to run the beach explorer. Credentials unlock the full detection pipeline.

```bash
# 1 — clone & enter the repo
git clone https://github.com/your-org/sargapp.git
cd sargapp

# 2 — create a Python 3.11 virtual environment
python3.11 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3 — install all dependencies
pip install -r requirements.txt

# 4 — copy the env template and fill in your values (see table below)
cp .env.example .env

# 5 — provision the database (run once in Supabase SQL editor)
#     open sql/schema.sql and execute it in your Supabase project

# 6 — seed the beach catalog into Supabase
python -m scripts.seed_beaches

# 7 — launch the beach explorer (works offline — no API needed)
streamlit run dashboard/beaches.py
```

Open http://localhost:8501 — you'll see the interactive tropical map with 56 DR beaches.

### Also run the API and risk map

```bash
# API (separate terminal)
uvicorn api.main:app --reload
# → http://localhost:8000/docs for interactive API docs

# Coastal risk map
streamlit run dashboard/app.py

# Full detection pipeline (requires EE + CMEMS credentials)
python -m pipeline.run
```

---

## Environment variables

Copy `.env.example` to `.env` and fill in every variable.
**Secrets are never hardcoded** — all config comes from the environment.

| Variable | Required for | Description |
|---|---|---|
| `EE_PROJECT` | Pipeline | Google Cloud project with Earth Engine enabled |
| `EE_SERVICE_ACCOUNT_JSON` | Pipeline | Path to service-account key file **or** the JSON string |
| `CMEMS_USERNAME` | Pipeline | Copernicus Marine username |
| `CMEMS_PASSWORD` | Pipeline | Copernicus Marine password |
| `SUPABASE_URL` | All | Supabase project URL |
| `SUPABASE_KEY` | All | Service-role key (pipeline/API) or anon key (read-only) |
| `TELEGRAM_BOT_TOKEN` | Alerts | Bot token from @BotFather |
| `API_BASE_URL` | Dashboard risk overlay | Public Render URL — leave blank to skip live risk |

> **Beach explorer with no credentials** — leave `API_BASE_URL` blank.
> The map loads from the built-in dataset; the sargassum risk badges show "no data".
> Set `SUPABASE_URL`/`SUPABASE_KEY` + `API_BASE_URL` to unlock live risk overlays.

---

## Architecture

```
┌────────────┐   ┌─────────────┐   ┌───────────────┐   ┌───────────┐   ┌────────────┐
│  detect.py │ → │  ocean.py   │ → │   drift.py    │ → │  store.py │ → │dispatch.py │
│  (GEE/FAI) │   │ (CMEMS cur) │   │ (Lagrangian)  │   │ (Supabase)│   │ (Telegram) │
└────────────┘   └─────────────┘   └───────────────┘   └───────────┘   └────────────┘
                                                               │
                              ┌────────────────────────────────┤
                              ▼                                ▼
                       ┌────────────┐                  ┌───────────────┐
                       │  FastAPI   │                  │   Streamlit   │
                       │  api/      │                  │  dashboard/   │
                       └────────────┘                  └───────────────┘
```

### Repo layout

| Path | Purpose |
|---|---|
| `pipeline/` | detect → ocean → drift → store → dispatch → run |
| `api/` | FastAPI: health, zones, forecasts, beaches, subscriptions, Telegram webhook |
| `dashboard/app.py` | Streamlit coastal risk map |
| `dashboard/beaches.py` | Streamlit beach explorer (tropical UI, bilingual ES/EN) |
| `dashboard/beaches_data.py` | 56-beach offline dataset |
| `dashboard/risk_overlay.py` | Live risk fetch + nearest-zone mapping |
| `sql/schema.sql` | PostGIS tables + seed zones + indexes |
| `scripts/seed_beaches.py` | Idempotent Supabase beach seeder |
| `.github/workflows/` | `pipeline.yml` (cron every 6 h) + `keepalive.yml` (daily ping) |

---

## API reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness + DB connectivity |
| `GET` | `/zones` | All monitored coastal zones |
| `GET` | `/forecast` | Latest forecast for every zone |
| `GET` | `/forecast/{zone_id}` | Latest forecast for one zone |
| `GET` | `/beaches` | Beach catalog (`?province=` / `?region=` filters) |
| `POST` | `/subscribe` | Subscribe a Telegram chat to a zone |
| `POST` | `/telegram/webhook` | Telegram update handler |

Interactive docs at `/docs` when the API is running.

---

## Database schema

| Table | Description |
|---|---|
| `zones` | Coastal monitoring zones (PostGIS polygons + centre points) |
| `detections` | Sargassum patches detected from satellite imagery |
| `forecasts` | Zone-level risk level + ETA per pipeline run |
| `subscribers` | Telegram subscribers per zone |
| `beaches` | 56 DR beaches with metadata + PostGIS point geometry |

Risk levels: `none` · `low` · `medium` · `high`

---

## Telegram bot commands

Once deployed and webhook registered:

| Command | Action |
|---|---|
| `/start` | Welcome message + usage |
| `/subscribe <zone>` | Subscribe to alerts for a zone (e.g. `/subscribe Punta Cana`) |
| `/status` | Show current risk for all zones |
| `/stop` | Unsubscribe from all alerts |

---

## Deployment

| Service | What runs there | Free tier |
|---|---|---|
| **Render** | FastAPI (`api/`) | 750 h/month free web service |
| **Streamlit Community Cloud** | `dashboard/beaches.py` or `dashboard/app.py` | Free |
| **Supabase** | Postgres + PostGIS | 500 MB free |
| **GitHub Actions** | Pipeline cron + keep-alive | 2 000 min/month free |

See `render.yaml` for the Render service definition.

---

## Tech stack

- **Language:** Python 3.11
- **Detection:** earthengine-api · geemap · geopandas · shapely · numpy
- **Ocean data:** copernicusmarine
- **Database:** Supabase (Postgres + PostGIS) via supabase-py
- **API:** FastAPI · uvicorn · pydantic
- **Bot:** python-telegram-bot / httpx
- **Dashboard:** streamlit · folium · streamlit-folium · pydeck
- **Scheduling:** GitHub Actions
- **Config:** python-dotenv

---

## How sargassum risk is calculated

The pipeline runs four steps automatically every 6 hours via GitHub Actions.

### Step 1 — Detection (Sentinel-2 / FAI)

Satellite imagery is pulled from the **Copernicus Sentinel-2 SR Harmonized** collection via Google Earth Engine.
Only scenes with cloud cover ≤ 40 % are used.
For each pixel inside the DR Exclusive Economic Zone (lat 17.3–21.5 °N, lon 72.1–67.3 °W) the **Floating Algae Index (FAI)** is computed:

$$
\text{FAI} = \rho_\text{NIR} - \left( \rho_\text{Red} + (\rho_\text{SWIR} - \rho_\text{Red}) \cdot \frac{\lambda_\text{NIR} - \lambda_\text{Red}}{\lambda_\text{SWIR} - \lambda_\text{Red}} \right)
$$

Pixels with FAI > threshold (default **0.02**, tunable via `FAI_THRESHOLD` env var) are flagged as floating biomass.
Contiguous flagged pixels are vectorised into patch polygons. Patches smaller than 0.05 km² are discarded as noise.

### Step 2 — Ocean forcing (Copernicus Marine / Open-Meteo)

For up to 20 patch centroids the pipeline fetches:

| Source | Variable | Horizon |
|---|---|---|
| **Copernicus Marine** (`GLOBAL_ANALYSISFORECAST_PHY`) | Eastward current `uo`, northward current `vo` (m/s) | 72 h |
| **Open-Meteo** (free tier) | 10 m zonal wind `u`, meridional wind `v` (m/s) | 72 h |

### Step 3 — Drift model (Lagrangian advection)

Each patch centroid is stepped forward **1 hour at a time** over 72 hours:

$$
u_\text{eff} = u_\text{current} + \alpha \cdot u_\text{wind}
\qquad
v_\text{eff} = v_\text{current} + \alpha \cdot v_\text{wind}
$$

$$
\text{lon}_{t+1} = \text{lon}_t + \frac{u_\text{eff} \cdot \Delta t}{\cos(\text{lat}_t) \cdot 111\,320}
\qquad
\text{lat}_{t+1} = \text{lat}_t + \frac{v_\text{eff} \cdot \Delta t}{111\,320}
$$

where $\Delta t = 3600$ s and $\alpha = 0.02$ (2 % windage — empirical value for surface biomass, tunable via `WIND_DRIFT_FACTOR`).

When a patch centroid enters a **zone bounding box** (0.1 ° half-width square around each coastal zone centre), the arrival hour and projected area are recorded.

### Step 4 — Risk classification

For each coastal zone the pipeline accumulates the total patch area projected to arrive within 72 hours and the earliest arrival time (ETA):

| Risk level | Condition |
|---|---|
| `none` | No patch projected to reach the zone within 72 h |
| `low` | Projected area < **1 km²** |
| `medium` | Projected area **1–10 km²** |
| `high` | Projected area > **10 km²** OR ETA ≤ **24 h** |

Thresholds are tunable via `RISK_AREA_LOW_MAX_KM2`, `RISK_AREA_MEDIUM_MAX_KM2`, and `RISK_HIGH_ETA_HOURS` in `pipeline/config.py`.

### What the dashboard shows

Each beach on the map is matched to the **nearest monitoring zone** by Haversine distance.
The zone's risk level, ETA, and projected arrival timestamp are displayed in the beach detail panel.
Forecasts are refreshed every 6 hours when the pipeline runs.

---

## Development notes

- Secrets live in `.env` — never commit them to Git (`.gitignore` covers `.env`)
- The beach explorer runs fully offline; live risk badges require `API_BASE_URL`
- One failed pipeline step logs an error but does not crash the whole run
- All times stored as UTC; Telegram alerts display local DR time (UTC−4)
