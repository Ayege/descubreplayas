# Sargassum POC — C4 Architecture & Process Diagrams (Mermaid)

All diagrams are plain Mermaid source. Render them by:

* Pasting into **GitHub** (renders Mermaid in `.md` natively)
* **mermaid.live** (live editor + PNG/SVG export)
* **VS Code** with the *Markdown Preview Mermaid Support* extension
* Any docs tool that supports Mermaid (Docusaurus, MkDocs Material, Notion, Obsidian)

> Note: Mermaid's C4 support is still marked experimental. The diagrams below use stable syntax, but if your renderer is older and a C4 block fails, update Mermaid to v10+ or use [mermaid.live](https://mermaid.live/). For pixel-perfect C4 you can also paste the same structure into Structurizr DSL or PlantUML later — the model maps 1:1.

---

## Level 1 — System Context

The big picture: who uses the system and which external services it depends on.

```mermaid
C4Context
    title System Context — Sargassum Detection & Early Warning System

    Person(fisher, "Artisanal Fisherman", "Operates a small boat; avoids sargassum near shore, finds fishing hotspots")
    Person(hotel, "Hotel Operator", "Manages a coastal resort; needs lead time to deploy barriers")
    Person(muni, "Municipality / Tourism Board", "Plans beach cleanup and public communication")

    System(ews, "Sargassum EWS", "Detects sargassum from satellites, forecasts drift, pushes lightweight alerts")

    System_Ext(gee, "Google Earth Engine", "Satellite imagery catalog + compute (Sentinel-2/3)")
    System_Ext(cmems, "Copernicus Marine Service", "Ocean currents and wind forecasts")
    System_Ext(tg, "Telegram", "Messaging platform for alert delivery")

    Rel(ews, gee, "Fetches imagery & computes indices", "EE Python API")
    Rel(ews, cmems, "Fetches currents & wind", "copernicusmarine")
    Rel(ews, tg, "Sends alerts via", "Bot API / HTTPS")
    Rel(fisher, tg, "Receives alerts & queries status", "Telegram app")
    Rel(hotel, ews, "Views forecasts", "Dashboard / API")
    Rel(muni, ews, "Views forecasts", "Dashboard")

    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

---

## Level 2 — Container

The runnable pieces and how data moves between them. Each container is something you deploy.

```mermaid
C4Container
    title Container Diagram — Sargassum EWS

    Person(fisher, "Fisherman", "Boat operator")
    Person(hotel, "Hotel Operator", "Coastal resort")

    System_Boundary(ews, "Sargassum EWS") {
        Container(pipeline, "Detection & Forecast Pipeline", "Python, run by GitHub Actions cron", "Every 6h: detect patches, model drift, compute ETA, store, dispatch alerts")
        ContainerDb(db, "Operational Database", "Supabase — Postgres + PostGIS", "Detections, forecasts, zones, subscribers")
        Container(api, "Forecast API", "FastAPI on Render", "Serves zone forecasts; hosts the Telegram webhook")
        Container(bot, "Telegram Bot Logic", "python-telegram-bot / httpx", "Subscribe, status, alert delivery")
        Container(dash, "Dashboard", "Streamlit Community Cloud", "Live risk map for stakeholders")
    }

    System_Ext(gee, "Google Earth Engine", "Imagery + compute")
    System_Ext(cmems, "Copernicus Marine", "Ocean forcing")
    System_Ext(tg, "Telegram Platform", "Messaging")

    Rel(pipeline, gee, "Fetches imagery, computes FAI/NDVI", "EE Python API")
    Rel(pipeline, cmems, "Fetches currents & wind", "HTTPS")
    Rel(pipeline, db, "Writes detections & forecasts", "SQL")
    Rel(pipeline, bot, "Triggers high-risk alerts", "Bot token")
    Rel(api, db, "Reads forecasts; writes subscribers", "SQL")
    Rel(bot, api, "Runs inside", "in-process")
    Rel(dash, api, "Reads forecasts", "HTTPS / JSON")
    Rel(api, tg, "Registers webhook & sends messages", "Bot API")
    Rel(tg, api, "Delivers user commands", "Webhook")
    Rel(fisher, tg, "Subscribes / queries", "Telegram app")
    Rel(hotel, dash, "Views risk map", "Browser")

    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

---

## Level 3a — Component: Detection & Forecast Pipeline

Inside the pipeline container — the modules from the repo layout and the order the orchestrator calls them.

```mermaid
C4Component
    title Component Diagram — Detection & Forecast Pipeline

    Container_Boundary(pipeline, "Detection & Forecast Pipeline") {
        Component(run, "Orchestrator", "run.py", "Runs steps 1–6 in order; logging; idempotent")
        Component(detect, "Detector", "detect.py", "GEE fetch + cloud mask + FAI/NDVI threshold → patch polygons")
        Component(ocean, "Ocean Forcing", "ocean.py", "Fetches current & wind vectors at patch locations")
        Component(drift, "Drift Model", "drift.py", "Lagrangian advection → ETA & risk per zone")
        Component(store, "Persistence", "store.py", "Upserts detections & forecasts")
        Component(dispatch, "Alert Dispatcher", "dispatch.py", "Matches forecasts to subscribers, de-dupes, sends")
        Component(config, "Config", "config.py", "EEZ bbox, zones, thresholds, wind-drift factor")
    }

    ContainerDb(db, "Database", "Supabase Postgres+PostGIS", "")
    System_Ext(gee, "Google Earth Engine", "")
    System_Ext(cmems, "Copernicus Marine", "")
    Container(bot, "Telegram Bot", "", "")

    Rel(run, detect, "1. invokes")
    Rel(run, ocean, "3. invokes")
    Rel(run, drift, "4. invokes")
    Rel(run, store, "5. invokes")
    Rel(run, dispatch, "6. invokes")
    Rel(detect, gee, "queries", "EE API")
    Rel(detect, config, "reads thresholds")
    Rel(ocean, cmems, "queries", "HTTPS")
    Rel(drift, config, "reads zones & drift factor")
    Rel(store, db, "writes", "SQL")
    Rel(dispatch, db, "reads subscribers", "SQL")
    Rel(dispatch, bot, "sends alerts", "Bot token")

    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

---

## Level 3b — Component: Forecast API

Inside the FastAPI container — routes, schemas, DB client, and the Telegram handler.

```mermaid
C4Component
    title Component Diagram — Forecast API

    Container_Boundary(api, "Forecast API (FastAPI on Render)") {
        Component(routes, "REST Routes", "main.py", "/zones, /forecast, /subscribe, /health")
        Component(models, "Schemas", "models.py", "Pydantic request/response models")
        Component(dbc, "DB Client", "db.py", "Supabase connection & queries")
        Component(tgh, "Telegram Handler", "telegram.py", "Webhook parser, command handlers, send helper")
    }

    ContainerDb(db, "Database", "Supabase", "")
    System_Ext(tgp, "Telegram Platform", "")
    Container(dash, "Dashboard", "Streamlit", "")

    Rel(routes, models, "validates with")
    Rel(routes, dbc, "reads / writes via")
    Rel(tgh, dbc, "registers subscribers via")
    Rel(tgh, tgp, "sends replies", "Bot API")
    Rel(tgp, tgh, "delivers commands", "Webhook → /telegram/webhook")
    Rel(dbc, db, "SQL")
    Rel(dash, routes, "reads forecasts", "HTTPS / JSON")

    UpdateLayoutConfig($c4ShapeInRow="2", $c4BoundaryInRow="1")
```

---

## Level 4 — Deployment (free-tier hosting)

Where each container physically runs and the cost posture of each node.

```mermaid
C4Deployment
    title Deployment Diagram — Free-Tier POC

    Deployment_Node(gh, "GitHub", "SaaS — free") {
        Deployment_Node(gha, "GitHub Actions", "Scheduled runner, free minutes") {
            Container(pipeline, "Pipeline Job", "Python", "Runs every 6h via cron")
        }
    }
    Deployment_Node(render, "Render", "Free web service") {
        Container(api, "FastAPI App", "uvicorn", "Public HTTPS; cold-start ~40s")
    }
    Deployment_Node(supa, "Supabase", "Free project") {
        ContainerDb(db, "Postgres + PostGIS", "", "500MB; idle-pause guarded by keep-alive ping")
    }
    Deployment_Node(stc, "Streamlit Cloud", "Free app") {
        Container(dash, "Dashboard", "Streamlit", "")
    }
    Deployment_Node(google, "Google Cloud", "EE noncommercial tier") {
        Container(gee, "Earth Engine", "", "Imagery + compute (EECU quota)")
    }
    Deployment_Node(merc, "Mercator / Copernicus", "Free account") {
        Container(marine, "Marine Service", "", "Currents + wind")
    }

    Rel(pipeline, gee, "EE Python API")
    Rel(pipeline, marine, "HTTPS")
    Rel(pipeline, db, "SQL")
    Rel(api, db, "SQL")
    Rel(dash, api, "HTTPS")
```

---

## Process — Pipeline run (sequence)

One scheduled run, steps 1–6, end to end.

```mermaid
sequenceDiagram
    autonumber
    participant Cron as GitHub Actions (cron)
    participant Run as Orchestrator (run.py)
    participant GEE as Google Earth Engine
    participant CM as Copernicus Marine
    participant Drift as Drift Model
    participant DB as Supabase
    participant Bot as Telegram Bot
    participant User as Subscriber

    Cron->>Run: Trigger every 6h
    Run->>GEE: Fetch Sentinel imagery (EEZ, last N days)
    GEE-->>Run: Cloud-masked tiles
    Run->>Run: Compute FAI/NDVI, threshold → patches
    Run->>CM: Request currents + wind at patch points
    CM-->>Run: u/v vectors, 72h horizon
    Run->>Drift: Advect patches 24 / 48 / 72h
    Drift-->>Run: Zone forecasts (risk + ETA)
    Run->>DB: Upsert detections + forecasts
    Run->>DB: Query subscribers for high-risk zones
    DB-->>Run: Matching subscribers
    Run->>Bot: Send alerts (de-duplicated)
    Bot->>User: "Sargassum ETA Zone X in ~Yh"
```

---

## Process — Subscribe & status query (sequence)

The on-demand path users hit between pipeline runs.

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant TG as Telegram Platform
    participant API as FastAPI (/telegram/webhook)
    participant DB as Supabase

    U->>TG: /subscribe Punta Cana
    TG->>API: Webhook update
    API->>DB: Insert subscriber (chat_id, zone)
    DB-->>API: ok
    API->>TG: "Subscribed to Punta Cana"
    TG->>U: Confirmation

    U->>TG: /status
    TG->>API: Webhook update
    API->>DB: Latest forecast for user's zone
    DB-->>API: risk + ETA
    API->>TG: "Punta Cana: risk MED, ETA ~36h"
    TG->>U: Forecast reply
```

---

## Process — Build & deploy order (flowchart)

The order that avoids circular dependencies between services.

```mermaid
flowchart TD
    A[Scaffold repo + create accounts] --> B[Apply schema to Supabase]
    B --> C[Build & test pipeline locally]
    C --> D[Push: GitHub Actions cron runs pipeline]
    D --> E{Rows appear in Supabase?}
    E -- no --> C
    E -- yes --> F[Deploy FastAPI to Render]
    F --> G[Register Telegram webhook on Render URL]
    G --> H[Wire alert dispatch into pipeline]
    H --> I[Deploy Streamlit dashboard]
    I --> J[Add keep-alive cron hitting /health]
    J --> K([POC live — about $0/mo])
```

---

## Process — Forecast lifecycle (state)

How a detection moves from a pixel signature to an alert and out.

```mermaid
stateDiagram-v2
    [*] --> Detected: FAI/NDVI threshold hit
    Detected --> Forecast: drift model assigns zone + ETA
    Forecast --> LowRisk: small area / far ETA
    Forecast --> HighRisk: large area / ETA < 72h
    HighRisk --> Alerted: subscribers notified
    Alerted --> Arrived: ETA reached
    LowRisk --> Expired: no longer detected
    Arrived --> [*]
    Expired --> [*]
```

---

## Mapping back to the repo

| Diagram element  | File / location                    |
| ---------------- | ---------------------------------- |
| Orchestrator     | `pipeline/run.py`                |
| Detector         | `pipeline/detect.py`             |
| Ocean Forcing    | `pipeline/ocean.py`              |
| Drift Model      | `pipeline/drift.py`              |
| Persistence      | `pipeline/store.py`              |
| Alert Dispatcher | `pipeline/dispatch.py`           |
| Config           | `pipeline/config.py`             |
| REST Routes      | `api/main.py`                    |
| Schemas          | `api/models.py`                  |
| DB Client        | `api/db.py`                      |
| Telegram Handler | `api/telegram.py`                |
| Dashboard        | `dashboard/app.py`               |
| Schema           | `sql/schema.sql`                 |
| Cron             | `.github/workflows/pipeline.yml` |
| Deploy config    | `render.yaml`                    |

Keep these diagrams in `docs/architecture.md` in the repo so they render on GitHub and stay next to the code they describe.
