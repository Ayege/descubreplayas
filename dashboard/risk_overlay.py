"""Live sargassum-risk overlay helpers for the beach map (offline-testable).

Pure functions for nearest-zone matching plus a thin API fetch helper.
No Streamlit import here so the logic can be unit-tested without a UI.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

# Risk level -> hex colour, shared with the sargassum dashboard.
RISK_COLORS = {
    "none": "#6c757d",    # grey
    "low": "#28a745",     # green
    "medium": "#fd7e14",  # orange
    "high": "#dc3545",    # red
}
RISK_EMOJI = {"none": "⚪", "low": "🟢", "medium": "🟠", "high": "🔴"}

# Order risk by severity for sensible defaults / comparisons.
RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_zone(lat: float, lon: float, zones: list[dict]) -> Optional[dict]:
    """Return the zone dict (with center_lat/center_lon) nearest to a point."""
    if not zones:
        return None
    return min(
        zones,
        key=lambda z: haversine_km(lat, lon, z["center_lat"], z["center_lon"]),
    )


def risk_for_beach(
    beach: dict,
    zones: list[dict],
    risk_by_zone_id: dict[int, str],
) -> tuple[str, Optional[dict], float]:
    """Return (risk_level, nearest_zone, distance_km) for one beach.

    Falls back to risk 'none' when no forecast exists for the nearest zone.
    """
    zone = nearest_zone(beach["latitude"], beach["longitude"], zones)
    if zone is None:
        return "none", None, math.inf
    dist = haversine_km(beach["latitude"], beach["longitude"], zone["center_lat"], zone["center_lon"])
    risk = risk_by_zone_id.get(zone["id"], "none")
    return risk, zone, dist


def fetch_live_risk(api_base_url: str, timeout: int = 8) -> tuple[list[dict], dict[int, str]]:
    """Fetch zones and latest forecasts from the API.

    Returns (zones, {zone_id: risk_level}). Returns ([], {}) on any failure so
    the caller can degrade gracefully to region colouring.
    """
    if not api_base_url or not api_base_url.startswith(("http://", "https://")):
        logger.debug("API_BASE_URL not configured; skipping live risk fetch.")
        return [], {}

    try:
        import certifi
        import requests
    except Exception:
        logger.warning("requests/certifi unavailable; cannot fetch live risk.")
        return [], {}

    try:
        verify = certifi.where()
        zones = requests.get(f"{api_base_url}/zones", timeout=timeout, verify=verify).json()
        forecasts = requests.get(f"{api_base_url}/forecast", timeout=timeout, verify=verify).json()
    except Exception:
        logger.warning("Live risk fetch failed for %s.", api_base_url, exc_info=True)
        return [], {}

    risk_by_zone_id: dict[int, str] = {
        f["zone_id"]: f.get("risk_level", "none") for f in (forecasts or [])
    }
    return zones or [], risk_by_zone_id
