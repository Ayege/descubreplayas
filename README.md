# Sargassum Detection & Early-Warning System (POC)

A proof-of-concept that detects floating sargassum from satellite imagery over
the Dominican Republic Exclusive Economic Zone (EEZ), models its drift using
ocean currents and wind, computes an arrival ETA for a handful of coastal
zones, stores the results, and pushes lightweight Telegram alerts to fishermen
and hotels. Designed to run end-to-end on free tiers (~$0/month).

## Architecture

```
detect  -> ocean   -> drift   -> store    -> dispatch
(GEE)      (CMEMS)    (advect)   (Supabase)  (Telegram)
```

- **pipeline/** — detection, ocean data, drift modelling, storage, alert dispatch, orchestrator
- **api/** — FastAPI service (forecast/zone endpoints + Telegram webhook)
- **dashboard/** — Streamlit risk map
- **sql/** — PostGIS schema and seed zones
- **.github/workflows/** — scheduled pipeline (cron) + keep-alive ping

## Tech stack

- Python 3.11
- Detection: earthengine-api, geemap, geopandas, shapely, numpy
- Ocean data: copernicusmarine
- Database: Supabase (Postgres + PostGIS) via supabase-py
- API: FastAPI + uvicorn + pydantic
- Bot: python-telegram-bot / httpx
- Dashboard: streamlit + folium/pydeck
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

4. **Provision the database** — run `sql/schema.sql` against your Supabase
   project (Postgres + PostGIS) to create tables and seed the coastal zones.

## Running locally

> Logic is not implemented yet — this is the scaffold.

- Pipeline: `python -m pipeline.run`
- API: `uvicorn api.main:app --reload`
- Dashboard: `streamlit run dashboard/app.py`

## Coastal zones (seed)

| Zone | Lat | Lon |
| --- | --- | --- |
| Punta Cana | 18.58 | -68.37 |
| Bavaro | 18.68 | -68.43 |
| Samana | 19.20 | -69.33 |
| Puerto Plata | 19.80 | -70.69 |
| Juan Dolio | 18.43 | -69.42 |

## Status

Scaffolding complete. Module logic to be implemented in subsequent steps.
