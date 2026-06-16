# Sargassum Detection & Early-Warning System (POC)

A proof-of-concept that detects floating sargassum from satellite imagery over
the Dominican Republic Exclusive Economic Zone (EEZ), models its drift using
ocean currents and wind, computes an arrival ETA for coastal zones, stores the
results in Supabase, and sends lightweight Telegram alerts to fishermen and
hotels. This repo also includes a tourist-focused beach explorer with live
sargassum risk overlay.

## Architecture

```
detect  -> ocean   -> drift   -> store    -> dispatch
(GEE)      (CMEMS)    (advect)   (Supabase)  (Telegram)
```

- **pipeline/** — detection, ocean data fetch, drift modelling, storage, alert dispatch, orchestrator
- **api/** — FastAPI service for health, zones, forecasts, beaches, subscriptions, and Telegram webhook
- **dashboard/** — Streamlit risk map and DR beach explorer with live risk overlay
- **sql/** — PostGIS schema + seed zones + beach table definition
- **scripts/** — Supabase beach seeder
- **.github/workflows/** — scheduled pipeline cron and keep-alive ping

## Tech stack

- Python 3.11
- Detection: earthengine-api, geemap, geopandas, shapely, numpy
- Ocean data: copernicusmarine
- Database: Supabase (Postgres + PostGIS) via supabase-py
- API: FastAPI + uvicorn + pydantic
- Bot: python-telegram-bot / httpx
- Dashboard: streamlit + folium + streamlit-folium
- Scheduling: GitHub Actions (cron)
- Hosting: Render (API) + Streamlit Community Cloud (dashboard)

## Setup

1. **Clone and create a virtual environment** (Python 3.11):

   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables:**

   ```bash
   cp .env.example .env
   ```

   Then fill in every value in `.env`:

   | Variable | Description |
   | --- | --- |
   | `EE_PROJECT` | Google Cloud project id with Earth Engine enabled |
   | `EE_SERVICE_ACCOUNT_JSON` | Path to service-account key file OR the JSON string |
   | `CMEMS_USERNAME` / `CMEMS_PASSWORD` | Copernicus Marine credentials |
   | `SUPABASE_URL` | Supabase project URL |
   | `SUPABASE_KEY` | Service-role key (pipeline) / anon key (read API) |
   | `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
   | `API_BASE_URL` | Public Render URL (dashboard + webhook registration) |

   Secrets are **never** hardcoded — everything is read from environment variables.

4. **Provision the database** — run `sql/schema.sql` in Supabase to create tables and seed the coastal zones.

5. **Seed the beach catalog** — after schema creation, run:

   ```bash
   python -m scripts.seed_beaches
   ```

## Running locally

- Pipeline: `python -m pipeline.run`
- API: `uvicorn api.main:app --reload`
- Dashboard risk map: `streamlit run dashboard/app.py`
- Beach explorer: `streamlit run dashboard/beaches.py`

## API endpoints

- `GET /health` — liveness check + DB connectivity
- `GET /zones` — all monitored coastal zones
- `GET /forecast` — latest forecast for each zone
- `GET /forecast/{zone_id}` — latest forecast for one zone
- `GET /beaches` — DR beach catalog, optional `?province=` / `?region=` filters
- `POST /subscribe` — subscribe a chat to a zone
- `POST /telegram/webhook` — Telegram update handler

## Database schema highlights

Tables currently managed by `sql/schema.sql`:

- `zones` — coastal monitoring zones with seed geometry and centre points
- `detections` — detected sargassum polygons from satellite imagery
- `forecasts` — zone-level risk forecasts and estimated arrival times
- `subscribers` — Telegram subscribers for zone alerts
- `beaches` — DR tourism beaches with metadata, coordinates, and PostGIS point geometry

## Features implemented

- end-to-end pipeline scaffold for sargassum detection, ocean forcing, drift, storage, and dispatch
- FastAPI backend serving health, zones, forecasts, beaches, and subscription APIs
- Streamlit coastal risk map with live zone forecast overlay
- Streamlit beach explorer with 50+ Dominican Republic beaches
- live sargassum risk overlay for beaches using nearest monitored zone
- idempotent Supabase beach seeder (`scripts/seed_beaches.py`)
- expanded `sql/schema.sql` with `beaches` table and spatial indexes

## Current status

- Pipeline modules exist and can be run locally
- API is ready to serve zone, forecast, and beach data
- Beach explorer is implemented and can render offline data plus live risk
- The project now includes both coastal monitoring and tourist beach discovery

## Notes

- Live dashboard risk overlay requires a running API and a configured `API_BASE_URL`
- Use `.env` for credentials, and never check secrets into Git
- The beach explorer is intentionally offline by default; live sargassum risk is optional
