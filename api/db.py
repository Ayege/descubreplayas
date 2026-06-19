"""Supabase client setup and database query helpers for the API."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from supabase import create_client, Client

from pipeline import config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Return a cached Supabase client (created once per process)."""
    if not config.SUPABASE_URL or not config.SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set.")
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def ping() -> bool:
    """Return True if the DB is reachable (used by /health)."""
    try:
        get_client().table("zones").select("id").limit(1).execute()
        return True
    except Exception:
        logger.exception("DB ping failed.")
        return False


def list_zones() -> list[dict[str, Any]]:
    result = get_client().table("zones").select("id, name, center_lat, center_lon").order("id").execute()
    return result.data or []


def latest_forecasts() -> list[dict[str, Any]]:
    """Return the single most-recent forecast per zone."""
    # Supabase / PostgREST doesn't support DISTINCT ON directly, so we fetch
    # recent rows and deduplicate in Python. 11 zones × 4 runs = 44 worst-case;
    # limit=50 is enough with a small safety margin.
    result = (
        get_client()
        .table("forecasts")
        .select("id, run_at, zone_id, risk_level, eta_hours, eta_timestamp, horizons, zones(name)")
        .order("run_at", desc=True)
        .limit(50)
        .execute()
    )
    rows = result.data or []
    seen: set[int] = set()
    deduped = []
    for row in rows:
        zid = row["zone_id"]
        if zid not in seen:
            seen.add(zid)
            deduped.append(row)
    return deduped


def latest_forecast_for_zone(zone_id: int) -> dict[str, Any] | None:
    result = (
        get_client()
        .table("forecasts")
        .select("id, run_at, zone_id, risk_level, eta_hours, eta_timestamp, horizons, zones(name)")
        .eq("zone_id", zone_id)
        .order("run_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def insert_subscriber(channel: str, chat_id: str, zone_id: int, role: str) -> dict[str, Any]:
    result = (
        get_client()
        .table("subscribers")
        .upsert(
            {"channel": channel, "chat_id": chat_id, "zone_id": zone_id, "role": role},
            on_conflict="channel,chat_id,zone_id",
        )
        .execute()
    )
    rows = result.data or []
    if not rows:
        logger.error(
            "insert_subscriber returned no data for chat_id=%s zone_id=%s", chat_id, zone_id
        )
        raise RuntimeError("Subscription insert returned no data.")
    return rows[0]


def list_beaches(province: str | None = None, region: str | None = None) -> list[dict[str, Any]]:
    """Return beaches, optionally filtered by province or region."""
    query = get_client().table("beaches").select(
        "id, name, province, region, latitude, longitude, access_type, access_description, "
        "entrance_fee, parking, beach_type, activities, wildlife, ecosystem, protected_area, "
        "facilities, water_conditions, best_time_to_visit, description, google_maps_url"
    )
    if province:
        query = query.eq("province", province)
    if region:
        query = query.eq("region", region)
    result = query.order("name").execute()
    return result.data or []


def list_ml_forecasts(lead_days: int | None = None) -> list[dict[str, Any]]:
    """Return the latest ML extended forecast per (zone, lead_days).

    Optionally filter to a single lead horizon (7, 14, or 21 days).
    """
    # 11 zones × 3 lead days = 33 rows max; limit=100 is sufficient with buffer.
    query = (
        get_client()
        .table("ml_forecasts")
        .select(
            "id, run_at, zone_id, lead_days, risk_level, confidence, method, valid_at, "
            "zones(name)"
        )
        .order("run_at", desc=True)
        .limit(100)
    )
    if lead_days is not None:
        query = query.eq("lead_days", lead_days)
    rows = query.execute().data or []

    # Deduplicate: keep only the latest row per (zone_id, lead_days).
    seen: set[tuple[int, int]] = set()
    deduped = []
    for row in rows:
        key = (row["zone_id"], row["lead_days"])
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def list_detections(limit: int = 2000) -> list[dict[str, Any]]:
    """Return the latest pipeline run's detected sargassum masses (lat/lon + area)."""
    result = (
        get_client()
        .table("detections_latest")
        .select("id, run_at, lat, lon, area_km2, source")
        .order("area_km2", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []
