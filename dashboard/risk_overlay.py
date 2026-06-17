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
    forecast_by_zone_id: dict[int, dict],
) -> tuple[str, "Optional[dict]", float, "Optional[dict]"]:
    """Return (risk_level, nearest_zone, distance_km, forecast) for one beach.

    Falls back to risk 'none' when no forecast exists for the nearest zone.
    """
    zone = nearest_zone(beach["latitude"], beach["longitude"], zones)
    if zone is None:
        return "none", None, math.inf, None
    dist = haversine_km(beach["latitude"], beach["longitude"], zone["center_lat"], zone["center_lon"])
    fc = forecast_by_zone_id.get(zone["id"])
    risk = fc.get("risk_level", "none") if fc else "none"
    return risk, zone, dist, fc


# ---------------------------------------------------------------------------
# Per-beach risk from ACTUAL detected masses (not zone-center approximation).
#
# The zone model answers "is there sargassum in this ~80 km coastal box?" — too
# coarse: two beaches in the same box always read the same, and the distance is
# measured to the box centre, not to the sargassum. These functions compute a
# beach-specific risk from the real detected masses: how much biomass is near
# THIS beach and how close the nearest mass actually is.
# ---------------------------------------------------------------------------

import os as _os  # noqa: E402

# Masses beyond this great-circle distance from a beach do not affect it.
# 25 km keeps the risk LOCAL to the beach: a raft 40 km offshore is not this
# beach's problem. (Was 45 km, which let distant masses dominate and made risk
# look uncorrelated with distance.)
BEACH_DETECT_RADIUS_KM = float(_os.environ.get("BEACH_DETECT_RADIUS_KM", "25"))
# A mass at/under this distance counts as an imminent, on-the-doorstep threat.
BEACH_IMMINENT_KM = float(_os.environ.get("BEACH_IMMINENT_KM", "8"))
# Proximity-weighted area thresholds (km²). Each mass contributes its area times
# a linear distance decay (full weight at the beach, zero at the radius), so a
# big raft far offshore counts for little while a small raft on the shoreline
# counts fully. Brackets mirror the pipeline's area logic at a per-beach scale.
BEACH_AREA_MIN_KM2 = float(_os.environ.get("BEACH_AREA_MIN_KM2", "0.3"))
BEACH_AREA_LOW_MAX_KM2 = float(_os.environ.get("BEACH_AREA_LOW_MAX_KM2", "3.0"))
BEACH_AREA_MEDIUM_MAX_KM2 = float(_os.environ.get("BEACH_AREA_MEDIUM_MAX_KM2", "12.0"))


def _classify_detection_risk(weighted_area_km2: float, nearest_km: float) -> str:
    """Classify per-beach risk from proximity-weighted nearby mass area + nearest distance."""
    if weighted_area_km2 < BEACH_AREA_MIN_KM2:
        return "none"
    # A meaningful mass that's already on the doorstep → high regardless of bracket.
    if weighted_area_km2 >= BEACH_AREA_LOW_MAX_KM2 and nearest_km <= BEACH_IMMINENT_KM:
        return "high"
    if weighted_area_km2 >= BEACH_AREA_MEDIUM_MAX_KM2:
        return "high"
    if weighted_area_km2 >= BEACH_AREA_LOW_MAX_KM2:
        return "medium"
    return "low"


def risk_from_detections(
    beach: dict,
    detections: list[dict],
    radius_km: float = BEACH_DETECT_RADIUS_KM,
) -> tuple[str, "Optional[dict]", float, float]:
    """Compute risk for one beach from the nearest ACTUAL detected sargassum masses.

    Args:
        beach: dict with at least "latitude" / "longitude".
        detections: list of {lat, lon, area_km2, ...} from the API.
        radius_km: only masses within this distance of the beach are considered.

    Returns:
        (risk_level, nearest_mass | None, nearest_km, weighted_area_km2)
        risk_level is 'none' when no detected mass lies within radius_km.
    """
    if not detections:
        return "none", None, math.inf, 0.0

    blat = float(beach["latitude"])
    blon = float(beach["longitude"])
    nearest: Optional[dict] = None
    nearest_km = math.inf
    weighted_area = 0.0

    for d in detections:
        try:
            dlat = float(d["lat"])
            dlon = float(d["lon"])
            area = float(d.get("area_km2", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        dist = haversine_km(blat, blon, dlat, dlon)
        if dist > radius_km:
            continue
        # QUADRATIC distance decay: a mass right at the beach counts fully, but
        # weight falls off sharply with distance (squared), so risk tracks
        # proximity. e.g. at 4 km weight ≈ 0.71, at 9 km ≈ 0.41, at 20 km ≈ 0.04.
        weight = max(0.0, 1.0 - dist / radius_km) ** 2
        weighted_area += area * weight
        if dist < nearest_km:
            nearest_km = dist
            nearest = d

    if nearest is None:
        return "none", None, math.inf, 0.0

    risk = _classify_detection_risk(weighted_area, nearest_km)
    return risk, nearest, nearest_km, weighted_area



def fetch_live_risk(api_base_url: str, timeout: int = 8) -> tuple[list[dict], dict[int, dict]]:
    """Fetch zones and latest forecasts from the API.

    Returns (zones, {zone_id: full_forecast_dict}). Returns ([], {}) on any failure so
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

    forecast_by_zone_id: dict[int, dict] = {
        f["zone_id"]: f for f in (forecasts or [])
    }
    return zones or [], forecast_by_zone_id


def fetch_detections(api_base_url: str, limit: int = 2000, timeout: int = 8) -> list[dict]:
    """Fetch the latest run's sargassum masses from the API.

    Returns a list of {id, run_at, lat, lon, area_km2, source} dicts, or [] on
    any failure so the dashboard can degrade gracefully.
    """
    if not api_base_url or not api_base_url.startswith(("http://", "https://")):
        return []
    try:
        import certifi
        import requests
    except Exception:
        logger.warning("requests/certifi unavailable; cannot fetch detections.")
        return []
    try:
        verify = certifi.where()
        resp = requests.get(
            f"{api_base_url}/detections",
            params={"limit": limit},
            timeout=timeout,
            verify=verify,
        )
        return resp.json() or []
    except Exception:
        logger.warning("Detection fetch failed for %s.", api_base_url, exc_info=True)
        return []
