"""FastAPI application and route definitions."""
from __future__ import annotations

import hmac
import json
import logging
import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware

from api import db
from api.models import (
    BeachResponse,
    DetectionResponse,
    ExtendedForecastResponse,
    ForecastResponse,
    HealthResponse,
    SubscribeRequest,
    SubscribeResponse,
    ZoneResponse,
)
from pipeline import config

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Sargassum Early Warning API",
    description="Coastal sargassum arrival risk forecasts for the Dominican Republic.",
    version="0.1.0",
    # Hide the /redoc and /openapi.json routes in production to reduce attack surface.
    # Remove these two lines if you want interactive docs on the deployed instance.
    # redoc_url=None,
    # openapi_url=None,
)

# ---------------------------------------------------------------------------
# Compression — gzip responses larger than 1 KB (helps /beaches + /detections)
# ---------------------------------------------------------------------------
app.add_middleware(GZipMiddleware, minimum_size=1024)

# ---------------------------------------------------------------------------
# CORS — allow the frontend domain (and localhost for local dev).
# Extend _ALLOWED_ORIGINS via the CORS_ORIGINS env var (comma-separated).
# ---------------------------------------------------------------------------
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
    # Only list headers the dashboard actually sends; "*" is too broad.
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)


# ---------------------------------------------------------------------------
# Security headers — injected on every response
# ---------------------------------------------------------------------------
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ---------------------------------------------------------------------------
# Global exception handler — returns a sanitised error without exposing
# internal stack traces or framework internals to callers.
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again later."},
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
# Zones  (static — zones change only when the pipeline adds one)
# ---------------------------------------------------------------------------

@app.get("/zones", response_model=list[ZoneResponse], tags=["data"])
def list_zones() -> list[ZoneResponse]:
    rows = db.list_zones()
    content = json.dumps([ZoneResponse(**r).model_dump() for r in rows])
    return Response(
        content=content,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=3600"},  # zones rarely change
    )


# ---------------------------------------------------------------------------
# Forecasts  (updated every 6 h by the pipeline cron)
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
    content = json.dumps([_format_forecast(r).model_dump() for r in rows])
    return Response(
        content=content,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"},  # fresh within 5 min
    )


# IMPORTANT: /forecast/extended MUST come before /forecast/{zone_id}.
# FastAPI matches routes in declaration order; if /{zone_id} is first, it
# tries to coerce "extended" to int, returns 422, and /forecast/extended
# is never reached.
@app.get(
    "/forecast/extended",
    response_model=list[ExtendedForecastResponse],
    tags=["data"],
)
def list_extended_forecasts(
    lead_days: int | None = Query(
        default=None,
        description="Filter by horizon. Must be 7, 14, or 21.",
    )
) -> list[ExtendedForecastResponse]:
    """Return the latest ML-based extended risk forecasts (7/14/21 days ahead)."""
    if lead_days is not None and lead_days not in (7, 14, 21):
        raise HTTPException(
            status_code=422,
            detail="lead_days must be 7, 14, or 21.",
        )
    rows = db.list_ml_forecasts(lead_days=lead_days)
    out = []
    for r in rows:
        zone_obj = r.get("zones") or {}
        zone_name = zone_obj.get("name", "") if isinstance(zone_obj, dict) else ""
        out.append(ExtendedForecastResponse(
            zone_id=r["zone_id"],
            zone_name=zone_name,
            lead_days=r["lead_days"],
            risk_level=r["risk_level"],
            confidence=r["confidence"],
            method=r["method"],
            valid_at=str(r["valid_at"]),
            run_at=str(r["run_at"]),
        ))
    content = json.dumps([e.model_dump() for e in out])
    return Response(
        content=content,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/forecast/{zone_id}", response_model=ForecastResponse, tags=["data"])
def get_forecast(zone_id: int) -> ForecastResponse:
    row = db.latest_forecast_for_zone(zone_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No forecast found for zone_id={zone_id}")
    return _format_forecast(row)


# ---------------------------------------------------------------------------
# Beaches  (static catalog — only changes when seed script runs)
# ---------------------------------------------------------------------------

_PROVINCE_MAX = 64
_REGION_MAX = 64

@app.get("/beaches", response_model=list[BeachResponse], tags=["data"])
def list_beaches(
    province: str | None = Query(default=None, max_length=_PROVINCE_MAX),
    region: str | None = Query(default=None, max_length=_REGION_MAX),
) -> list[BeachResponse]:
    """List tourism beaches, optionally filtered by ?province= or ?region=."""
    rows = db.list_beaches(province=province, region=region)
    content = json.dumps([BeachResponse(**r).model_dump() for r in rows])
    return Response(
        content=content,
        media_type="application/json",
        # Long TTL: beach catalog is static; pipeline never updates it.
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ---------------------------------------------------------------------------
# Detections (sargassum masses)
# ---------------------------------------------------------------------------

@app.get("/detections", response_model=list[DetectionResponse], tags=["data"])
def list_detections(
    limit: int = Query(default=800, ge=1, le=2000)
) -> list[DetectionResponse]:
    """Return the latest pipeline run's detected sargassum masses (lat/lon + area)."""
    rows = db.list_detections(limit=limit)
    content = json.dumps([DetectionResponse(**r).model_dump() for r in rows])
    return Response(
        content=content,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )


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
    if not row:
        raise HTTPException(status_code=503, detail="Subscription could not be saved. Try again.")
    return SubscribeResponse(**row)


# ---------------------------------------------------------------------------
# Telegram webhook
# ---------------------------------------------------------------------------

@app.post("/telegram/webhook", tags=["telegram"])
async def telegram_webhook(update: dict, request: Request) -> dict:
    """Receive Telegram Bot API update objects.

    The endpoint is public, so we authenticate the caller via the shared
    secret Telegram echoes in the X-Telegram-Bot-Api-Secret-Token header
    (configured when the webhook is registered). Requests without the correct
    secret are rejected to prevent spoofed updates from triggering the bot.
    """
    expected = config.TELEGRAM_WEBHOOK_SECRET
    if expected:
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(provided, expected):
            logger.warning("Rejected Telegram webhook with invalid secret token.")
            raise HTTPException(status_code=403, detail="Forbidden")
    else:
        logger.warning(
            "TELEGRAM_WEBHOOK_SECRET not set — webhook is unauthenticated. "
            "Set it and re-register the webhook to prevent spoofed updates."
        )
    try:
        from api.telegram import handle_update
        await handle_update(update)
    except Exception:
        logger.exception("telegram webhook handler failed")
    return {"ok": True}
