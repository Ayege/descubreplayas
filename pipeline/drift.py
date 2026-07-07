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

WIND_DRIFT_FACTOR (default 0.02 = 2.0 %) comes from empirical studies of
surface biomass windage.  Sargassum sits mostly below the waterline, so its
leeway is lower than a hollow buoy (~3 %) but non-negligible; published values
range 1.5–3 %.  Tune via the WIND_DRIFT_FACTOR env var (pipeline/config.py).
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


def _classify_risk(area_km2: float, eta_hours: Optional[int], horizon_hours: int) -> str:
    """Classify sargassum risk from projected area and arrival time.

    Both the *amount* of sargassum AND its proximity matter:

    * area_km2 < RISK_AREA_MIN_KM2  → none  (too small to impact a beach)
    * area_km2 in [min, low_max)    → low   (visible traces likely)
    * area_km2 in [low_max, med_max)→ medium (noticeable accumulation)
    * area_km2 >= med_max           → high  (significant beach impact)
    * large mass arriving very soon (eta <= HIGH_ETA_HOURS) → high

    A tiny speck (e.g. 0.1 km²) arriving in 2 hours is still only 'low',
    not 'high', because there is not enough biomass to matter.
    """
    if eta_hours is None or eta_hours > horizon_hours:
        return "none"
    if area_km2 < config.RISK_AREA_MIN_KM2:
        return "none"  # mass too small to produce meaningful beach impact
    # Large mass that's imminent → high regardless of exact area bracket
    if area_km2 >= config.RISK_AREA_LOW_MAX_KM2 and eta_hours <= config.RISK_HIGH_ETA_HOURS:
        return "high"
    if area_km2 >= config.RISK_AREA_MEDIUM_MAX_KM2:
        return "high"
    if area_km2 >= config.RISK_AREA_LOW_MAX_KM2:
        return "medium"
    return "low"  # area >= min but < low_max


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
        hours = [0, 24, 48, 72]

    horizons = sorted(set(hours))
    max_hours = max(horizons)
    alpha = config.WIND_DRIFT_FACTOR
    run_at = dt.datetime.now(dt.timezone.utc)

    # Pre-compute zone boxes (1-indexed to match DB id convention).
    zones = config.ZONES
    zone_boxes = [_zone_box(z) for z in zones]

    # zone_state[i] -> {"eta_hours": int|None, "arrivals": [(hour, area_km2), ...]}
    # We record the hour each patch first enters the zone plus its area, so we
    # can later compute the cumulative area present by ANY forecast horizon.
    zone_state: list[dict] = [
        {"eta_hours": None, "arrivals": []} for _ in zones
    ]

    for patch_idx, patch in enumerate(patches):
        f = forcing.get(patch_idx)
        if f is None:
            # No ocean forcing for this patch — fall back to zero drift.
            # The patch stays put, but the hour-0 immediate-presence check
            # below still correctly flags any mass already inside a zone.
            f = {"u_current": [0.0], "v_current": [0.0], "u_wind": [0.0], "v_wind": [0.0]}

        u_current: list[float] = f["u_current"]
        v_current: list[float] = f["v_current"]
        u_wind: Optional[list[float]] = f.get("u_wind")
        v_wind: Optional[list[float]] = f.get("v_wind")
        area_km2: float = float(patch.get("area_km2", 0.0))

        lon = float(patch["centroid_lon"])
        lat = float(patch["centroid_lat"])

        # Track which zones this patch has already been counted for, so its
        # area is added exactly once per zone (a patch may sit inside a zone
        # box for several consecutive hours).
        counted_zones: set[int] = set()

        # Hour 0 — a patch already inside a zone box is an IMMEDIATE risk.
        # Without this check the loop only registers patches that *enter* a
        # zone after drifting, missing biomass that is already present.
        for zi, box in enumerate(zone_boxes):
            if _in_zone(lon, lat, box):
                counted_zones.add(zi)
                st = zone_state[zi]
                st["arrivals"].append((0, area_km2))
                st["eta_hours"] = 0  # already in the zone now

        for step in range(max_hours):
            if len(counted_zones) == len(zone_boxes):
                break
            u_eff, v_eff = _effective_velocity(step, u_current, v_current, u_wind, v_wind, alpha)
            lon, lat = _step_position(lon, lat, u_eff, v_eff)

            hour = step + 1
            for zi, box in enumerate(zone_boxes):
                if zi in counted_zones:
                    continue
                if _in_zone(lon, lat, box):
                    counted_zones.add(zi)
                    st = zone_state[zi]
                    # Record arrival hour + area so cumulative area by any
                    # horizon can be derived afterwards.
                    st["arrivals"].append((hour, area_km2))
                    if st["eta_hours"] is None or hour < st["eta_hours"]:
                        st["eta_hours"] = hour


    # Build result list.
    results: list[dict] = []
    for zi, zone in enumerate(zones):
        state = zone_state[zi]
        eta_h = state["eta_hours"]
        arrivals: list[tuple[int, float]] = state["arrivals"]

        # Total area projected to be present within the full forecast window.
        total_area = sum(a for _, a in arrivals)

        # Per-horizon breakdown: cumulative area + risk classification at each
        # forecast checkpoint (e.g. 24h, 48h, 72h). This is the honest,
        # physics-limited forecast — risk evolving over the next 3 days.
        horizon_breakdown: list[dict] = []
        for h in horizons:
            area_h = sum(a for hr, a in arrivals if hr <= h)
            horizon_breakdown.append(
                {
                    "horizon_hours": h,
                    "risk_level": _classify_risk(area_h, eta_h, h),
                    "area_km2": round(area_h, 3),
                    "valid_at": (run_at + dt.timedelta(hours=h)).isoformat(),
                }
            )

        # Summary risk = worst risk across all horizons (so a zone that turns
        # high at +72h is still surfaced as high in the headline number).
        summary_risk = _classify_risk(total_area, eta_h, max_hours)

        eta_ts: Optional[str] = None
        if eta_h is not None:
            eta_ts = (run_at + dt.timedelta(hours=eta_h)).isoformat()

        results.append(
            {
                "zone_id": zi + 1,
                "zone_name": zone["name"],
                "risk_level": summary_risk,
                "eta_hours": eta_h,
                "eta_timestamp": eta_ts,
                "horizons": horizon_breakdown,
            }
        )
        logger.info(
            "Zone %s: risk=%s eta=%sh area=%.2fkm2 horizons=%s",
            zone["name"],
            summary_risk,
            eta_h,
            total_area,
            [(h["horizon_hours"], h["risk_level"]) for h in horizon_breakdown],
        )

    return results

