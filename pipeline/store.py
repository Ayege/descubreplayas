"""Storage step: upsert detections and forecasts into Supabase (Postgres + PostGIS)."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from supabase import create_client, Client

from pipeline import config

logger = logging.getLogger(__name__)


def _client() -> Client:
    """Return a Supabase service-role client."""
    if not config.SUPABASE_URL or not config.SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be set in the environment."
        )
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


# ---------------------------------------------------------------------------
# Detections
# ---------------------------------------------------------------------------

def upsert_detections(patches: list[dict]) -> list[dict]:
    """Insert detected sargassum patches into the detections table.

    Args:
        patches: list of dicts with at minimum:
                 {centroid_lat, centroid_lon, area_km2,
                  geom_wkt (polygon WKT string), source (optional)}

    Returns:
        List of inserted rows as returned by Supabase.
    """
    if not patches:
        logger.info("upsert_detections: nothing to insert.")
        return []

    run_at = dt.datetime.now(dt.timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for p in patches:
        geom_wkt = p.get("geom_wkt") or p.get("geometry_wkt", "")
        centroid_wkt = (
            f"POINT({p['centroid_lon']} {p['centroid_lat']})"
        )
        rows.append(
            {
                "run_at": run_at,
                "geom": f"SRID=4326;{geom_wkt}",
                "centroid": f"SRID=4326;{centroid_wkt}",
                "area_km2": float(p["area_km2"]),
                "source": p.get("source", config.DETECTION_SOURCE),
            }
        )

    try:
        sb = _client()
        result = sb.table("detections").insert(rows).execute()
        inserted = result.data or []
        logger.info("upsert_detections: inserted %d row(s).", len(inserted))
        return inserted
    except Exception:
        logger.exception("upsert_detections failed.")
        raise


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------

def upsert_forecasts(forecasts: list[dict]) -> list[dict]:
    """Insert zone forecasts into the forecasts table.

    Args:
        forecasts: list of dicts from drift.project_drift():
                   {zone_id, zone_name, risk_level, eta_hours, eta_timestamp}

    Returns:
        List of inserted rows as returned by Supabase.
    """
    if not forecasts:
        logger.info("upsert_forecasts: nothing to insert.")
        return []

    run_at = dt.datetime.now(dt.timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for f in forecasts:
        rows.append(
            {
                "run_at": run_at,
                "zone_id": int(f["zone_id"]),
                "risk_level": f["risk_level"],
                "eta_hours": f.get("eta_hours"),
                "eta_timestamp": f.get("eta_timestamp"),
                "horizons": f.get("horizons"),
            }
        )

    try:
        sb = _client()
        result = sb.table("forecasts").insert(rows).execute()
        inserted = result.data or []
        logger.info("upsert_forecasts: inserted %d row(s).", len(inserted))
        return inserted
    except Exception:
        logger.exception("upsert_forecasts failed.")
        raise


# ---------------------------------------------------------------------------
# Zone cache
# ---------------------------------------------------------------------------

def fetch_zone_id_map() -> dict[str, int]:
    """Return {zone_name -> id} from the zones table."""
    try:
        sb = _client()
        result = sb.table("zones").select("id, name").execute()
        return {row["name"]: row["id"] for row in (result.data or [])}
    except Exception:
        logger.exception("fetch_zone_id_map failed.")
        raise


def upsert_zones(zones: list[dict]) -> dict[str, int]:
    """Ensure all zones from config exist in the DB; insert any that are missing.

    Args:
        zones: list of {name, center_lat, center_lon} from config.ZONES.

    Returns:
        {zone_name -> id} map (all zones, including newly created ones).
    """
    sb = _client()
    existing = fetch_zone_id_map()
    missing = [z for z in zones if z["name"] not in existing]

    if missing:
        rows = []
        for z in missing:
            d = config.ZONE_BOX_HALF_DEG
            lat, lon = z["center_lat"], z["center_lon"]
            # Build a closed polygon ring: SW -> SE -> NE -> NW -> SW
            wkt = (
                f"POLYGON(({lon-d} {lat-d}, {lon+d} {lat-d}, "
                f"{lon+d} {lat+d}, {lon-d} {lat+d}, {lon-d} {lat-d}))"
            )
            rows.append({
                "name": z["name"],
                "center_lat": lat,
                "center_lon": lon,
                "geom": f"SRID=4326;{wkt}",
            })
        try:
            result = sb.table("zones").insert(rows).execute()
            for row in (result.data or []):
                existing[row["name"]] = row["id"]
            logger.info("upsert_zones: created %d new zone(s): %s",
                        len(missing), [z["name"] for z in missing])
        except Exception:
            logger.exception("upsert_zones: failed to insert missing zones.")
            raise
    else:
        logger.debug("upsert_zones: all %d zones already in DB.", len(zones))

    return existing
