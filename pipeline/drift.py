"""Drift step: Lagrangian advection of patches to compute arrival ETA and risk level per coastal zone.

Algorithm
---------
Each detected patch centroid is stepped forward one hour at a time:

    lon[t+1] = lon[t] + (u_eff * dt) / (cos(lat[t]) * METERS_PER_DEG)
    lat[t+1] = lat[t] + (v_eff * dt) / METERS_PER_DEG

where:
    u_eff = u_current + WIND_DRIFT_FACTOR * u_wind
    v_eff = v_current + WIND_DRIFT_FACTOR * v_wind
    dt    = 3600 s

WIND_DRIFT_FACTOR (default 0.015 = 1.5 %) comes from empirical studies of
surface biomass windage.  Sargassum sits mostly below the waterline, so its
leeway is lower than a hollow buoy (~3 %) but non-negligible.  Tune via env var.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Optional

from pipeline import config

logger = logging.getLogger(__name__)

# Earth geometry constant: metres per degree of latitude.
_METERS_PER_DEG = 111_320.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _zone_box(zone: dict) -> tuple[float, float, float, float]:
    """Return (min_lon, min_lat, max_lon, max_lat) for a zone bounding box."""
    d = config.ZONE_BOX_HALF_DEG
    lat, lon = zone["center_lat"], zone["center_lon"]
    return lon - d, lat - d, lon + d, lat + d


def _in_zone(lon: float, lat: float, box: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = box
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def _effective_velocity(
    step: int,
    u_current: list[float],
    v_current: list[float],
    u_wind: Optional[list[float]],
    v_wind: Optional[list[float]],
    alpha: float,
) -> tuple[float, float]:
    """Return (u_eff, v_eff) in m/s for a given timestep index."""
    idx = min(step, len(u_current) - 1)
    uc = u_current[idx]
    vc = v_current[idx]

    if u_wind and v_wind:
        widx = min(step, len(u_wind) - 1)
        uc += alpha * u_wind[widx]
        vc += alpha * v_wind[widx]

    return uc, vc


def _step_position(
    lon: float,
    lat: float,
    u_eff: float,
    v_eff: float,
    dt_sec: float = 3600.0,
) -> tuple[float, float]:
    """Advance (lon, lat) by one timestep using simple Lagrangian advection."""
    new_lat = lat + (v_eff * dt_sec) / _METERS_PER_DEG
    new_lon = lon + (u_eff * dt_sec) / (math.cos(math.radians(lat)) * _METERS_PER_DEG)
    return new_lon, new_lat


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def project_drift(
    patches: list[dict],
    forcing: dict[int, dict],
    hours: list[int] | None = None,
) -> list[dict]:
    """Project patch centroids forward and compute per-zone arrival risk.

    Args:
        patches: list of detection dicts, each with at least:
                 {"centroid_lat": float, "centroid_lon": float, "area_km2": float}
        forcing: output of ocean.get_ocean_forcing() — dict[point_index -> entry].
                 Patch i must have a corresponding entry in forcing[i].
        hours:   snapshot hours for trajectory output (default [24, 48, 72]).

    Returns:
        List of zone forecast dicts, one per zone:
        {
            "zone_id":       int (1-based index into config.ZONES),
            "zone_name":     str,
            "risk_level":    "none" | "low" | "medium" | "high",
            "eta_hours":     int | None,
            "eta_timestamp": str (ISO) | None,
        }
    """
    if hours is None:
        hours = [24, 48, 72]

    max_hours = max(hours)
    alpha = config.WIND_DRIFT_FACTOR
    run_at = dt.datetime.now(dt.timezone.utc)

    # Pre-compute zone boxes (1-indexed to match DB id convention).
    zones = config.ZONES
    zone_boxes = [_zone_box(z) for z in zones]

    # zone_state[i] -> {"eta_hours": int|None, "area_km2": float}
    zone_state: list[dict] = [{"eta_hours": None, "area_km2": 0.0} for _ in zones]

    for patch_idx, patch in enumerate(patches):
        f = forcing.get(patch_idx)
        if f is None:
            logger.warning("No forcing entry for patch %d; skipping.", patch_idx)
            continue

        u_current: list[float] = f["u_current"]
        v_current: list[float] = f["v_current"]
        u_wind: Optional[list[float]] = f.get("u_wind")
        v_wind: Optional[list[float]] = f.get("v_wind")
        area_km2: float = float(patch.get("area_km2", 0.0))

        lon = float(patch["centroid_lon"])
        lat = float(patch["centroid_lat"])

        for step in range(max_hours):
            u_eff, v_eff = _effective_velocity(step, u_current, v_current, u_wind, v_wind, alpha)
            lon, lat = _step_position(lon, lat, u_eff, v_eff)

            hour = step + 1
            for zi, box in enumerate(zone_boxes):
                if _in_zone(lon, lat, box):
                    prev = zone_state[zi]
                    if prev["eta_hours"] is None or hour < prev["eta_hours"]:
                        prev["eta_hours"] = hour
                        prev["area_km2"] += area_km2
                    elif prev["eta_hours"] == hour:
                        # Same earliest hour, accumulate area from other patches.
                        prev["area_km2"] += area_km2

    # Build result list.
    results: list[dict] = []
    for zi, zone in enumerate(zones):
        state = zone_state[zi]
        eta_h = state["eta_hours"]
        area = state["area_km2"]

        if eta_h is None:
            risk = "none"
        elif area > config.RISK_AREA_MEDIUM_MAX_KM2 or eta_h <= config.RISK_HIGH_ETA_HOURS:
            risk = "high"
        elif area > config.RISK_AREA_LOW_MAX_KM2:
            risk = "medium"
        else:
            risk = "low"

        eta_ts: Optional[str] = None
        if eta_h is not None:
            eta_ts = (run_at + dt.timedelta(hours=eta_h)).isoformat()

        results.append(
            {
                "zone_id": zi + 1,
                "zone_name": zone["name"],
                "risk_level": risk,
                "eta_hours": eta_h,
                "eta_timestamp": eta_ts,
            }
        )
        logger.info(
            "Zone %s: risk=%s eta=%sh area=%.2fkm2",
            zone["name"],
            risk,
            eta_h,
            area,
        )

    return results

