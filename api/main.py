"""FastAPI application and route definitions."""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api import db
from api.models import (
    BeachResponse,
    DetectionResponse,
    ForecastResponse,
    HealthResponse,
    SubscribeRequest,
    SubscribeResponse,
    ZoneResponse,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Sargassum Early Warning API",
    description="Coastal sargassum arrival risk forecasts for the Dominican Republic.",
    version="0.1.0",
)

# CORS — allow the frontend domain (and localhost for local dev).
# Extend _ALLOWED_ORIGINS via the CORS_ORIGINS env var (comma-separated).
_DEFAULT_ORIGINS = [
    "https://descubreplayas.com.do",
    "https://www.descubreplayas.com.do",
    "http://localhost:8501",   # local Streamlit
    "http://localhost:3000",   # local React/Next if used later
]
_extra = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
_ALLOWED_ORIGINS = _DEFAULT_ORIGINS + _extra

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Liveness + DB connectivity check (also used as Supabase keep-alive)."""
    db_status = "ok" if db.ping() else "error"
    return HealthResponse(status="ok", db=db_status)


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------

@app.get("/zones", response_model=list[ZoneResponse], tags=["data"])
def list_zones() -> list[ZoneResponse]:
    rows = db.list_zones()
    return [ZoneResponse(**r) for r in rows]


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------

def _format_forecast(row: dict) -> ForecastResponse:
    zone_name = row.get("zones") or {}
    if isinstance(zone_name, dict):
        zone_name = zone_name.get("name", "")
    return ForecastResponse(
        zone_id=row["zone_id"],
        name=zone_name,
        risk_level=row["risk_level"],
        eta_hours=row.get("eta_hours"),
        eta_timestamp=row.get("eta_timestamp"),
        run_at=str(row["run_at"]),
        horizons=row.get("horizons"),
    )


@app.get("/forecast", response_model=list[ForecastResponse], tags=["data"])
def list_forecasts() -> list[ForecastResponse]:
    rows = db.latest_forecasts()
    return [_format_forecast(r) for r in rows]


@app.get("/forecast/{zone_id}", response_model=ForecastResponse, tags=["data"])
def get_forecast(zone_id: int) -> ForecastResponse:
    row = db.latest_forecast_for_zone(zone_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No forecast found for zone_id={zone_id}")
    return _format_forecast(row)


# ---------------------------------------------------------------------------
# Beaches
# ---------------------------------------------------------------------------

@app.get("/beaches", response_model=list[BeachResponse], tags=["data"])
def list_beaches(province: str | None = None, region: str | None = None) -> list[BeachResponse]:
    """List tourism beaches, optionally filtered by ?province= or ?region=."""
    rows = db.list_beaches(province=province, region=region)
    return [BeachResponse(**r) for r in rows]


# ---------------------------------------------------------------------------
# Detections (sargassum masses)
# ---------------------------------------------------------------------------

@app.get("/detections", response_model=list[DetectionResponse], tags=["data"])
def list_detections(limit: int = 2000) -> list[DetectionResponse]:
    """Return the latest pipeline run's detected sargassum masses (lat/lon + area)."""
    rows = db.list_detections(limit=limit)
    return [DetectionResponse(**r) for r in rows]


# ---------------------------------------------------------------------------
# Subscribe
# ---------------------------------------------------------------------------

@app.post("/subscribe", response_model=SubscribeResponse, status_code=201, tags=["subscribe"])
def subscribe(body: SubscribeRequest) -> SubscribeResponse:
    row = db.insert_subscriber(
        channel=body.channel,
        chat_id=body.chat_id,
        zone_id=body.zone_id,
        role=body.role,
    )
    return SubscribeResponse(**row)


# ---------------------------------------------------------------------------
# Telegram webhook (stub — full handler lives in api/telegram.py)
# ---------------------------------------------------------------------------

@app.post("/telegram/webhook", tags=["telegram"])
async def telegram_webhook(update: dict) -> dict:
    """Receive Telegram Bot API update objects."""
    try:
        from api.telegram import handle_update
        await handle_update(update)
    except Exception:
        logger.exception("telegram webhook handler failed")
    return {"ok": True}
