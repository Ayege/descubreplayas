# Copyright 2026 Ayesha Yege
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Drift step: particle-ensemble advection to the coast, per-zone arrival risk.

Algorithm
---------
1. Each patch is released as DRIFT_PARTICLES particles spread over its
   outline (or around its centroid when there is no outline).
2. Particle k of every patch belongs to ensemble member k. Each member draws
   one windage value in [WIND_DRIFT_MIN, WIND_DRIFT_MAX], so a member is one
   coherent, plausible version of the next three days.
3. Each particle starts at the time its patch was OBSERVED (detections can
   be days old) and is stepped hourly up to now + forecast horizon:

       u_eff = u_current(t, x) + windage_k · u_wind(t, x)
       x    += u_eff · Δt + random walk (eddy diffusivity K)

   with the current and wind interpolated at the particle's CURRENT position
   and time from gridded fields (pipeline/ocean.py).
4. A particle stops when it comes within ARRIVAL_KM of the coast or lands on
   it. The stretch of coast it reaches belongs to the nearest zone centre
   (within ZONE_COAST_RADIUS_KM), so overlapping zones never share a beach
   and a mass passing offshore does not count as an arrival. Arrivals that
   happened before now (while catching up) count as already present (hour 0).
5. Per member and horizon, the arrived area (each patch's full area if its
   member-k particle arrived) is classified with the same thresholds as
   before. The share of members at each level is the probability; the
   headline level is the highest one reached by at least
   RISK_PROB_THRESHOLD of members.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

from pipeline import config

logger = logging.getLogger(__name__)

_METERS_PER_DEG = 111_320.0
_LAND_PATH = Path(__file__).parent / "data" / "land.geojson"
# Natural Earth DR outline, shared with the dashboard's land checks.
_DR_PATH = Path(__file__).parent.parent / "dashboard" / "dr_boundary.geojson"
_LEVELS = ("none", "low", "medium", "high")
# Local equal-distance projection for km distances around the DR (~18.9° N).
_KM_X = 111.32 * math.cos(math.radians(18.9))
_KM_Y = 111.32


# ---------------------------------------------------------------------------
# Risk classification (unchanged thresholds)
# ---------------------------------------------------------------------------

def _classify_risk(area_km2: float, significant_eta_hours: Optional[int],
                   min_km2: Optional[float] = None) -> str:
    """Classify sargassum risk from arrived area and how soon a significant
    amount of it arrives.

    * area_km2 < RISK_AREA_MIN_KM2  → none  (too small to impact a beach)
    * area_km2 in [min, low_max)    → low   (visible traces likely)
    * area_km2 in [low_max, med_max)→ medium (noticeable accumulation)
    * area_km2 >= med_max           → high  (significant beach impact)
    * a significant amount (>= RISK_AREA_LOW_MAX_KM2) arriving very soon
      (significant_eta_hours <= RISK_HIGH_ETA_HOURS) → high

    `significant_eta_hours` is the hour the CUMULATIVE arrived area first
    reached RISK_AREA_LOW_MAX_KM2 (see `_significant_eta`), not the nearest
    patch's ETA, so a trace arriving soon can't make an unrelated large mass
    arriving late read as imminent.

    `min_km2` overrides RISK_AREA_MIN_KM2 (beaches use a lower bar).
    """
    if area_km2 < (config.RISK_AREA_MIN_KM2 if min_km2 is None else min_km2):
        return "none"
    if (
        area_km2 >= config.RISK_AREA_LOW_MAX_KM2
        and significant_eta_hours is not None
        and significant_eta_hours <= config.RISK_HIGH_ETA_HOURS
    ):
        return "high"
    if area_km2 >= config.RISK_AREA_MEDIUM_MAX_KM2:
        return "high"
    if area_km2 >= config.RISK_AREA_LOW_MAX_KM2:
        return "medium"
    return "low"


def _significant_eta(
    arrivals: list[tuple[int, float]], upto_hour: int, threshold_km2: float
) -> Optional[int]:
    """Earliest hour (<= upto_hour) at which the cumulative arrived area
    reaches `threshold_km2`, or None."""
    cumulative = 0.0
    for hour, area in sorted((hr, a) for hr, a in arrivals if hr <= upto_hour):
        cumulative += area
        if cumulative >= threshold_km2:
            return hour
    return None


# ---------------------------------------------------------------------------
# Coastline
# ---------------------------------------------------------------------------

def _to_km(lat, lon) -> np.ndarray:
    return np.column_stack([np.asarray(lon, float) * _KM_X, np.asarray(lat, float) * _KM_Y])


@lru_cache(maxsize=1)
def _land():
    """Prepared land polygon (Hispaniola, Puerto Rico, nearby islands)."""
    import shapely
    from shapely.geometry import shape

    with open(_LAND_PATH, encoding="utf-8") as fh:
        geom = shape(json.load(fh)["geometry"])
    shapely.prepare(geom)
    return geom


@lru_cache(maxsize=4)
def _coast(zone_key: tuple) -> tuple:
    """(KD-tree over coast points in km, zone index per point or -1).

    Coast points are the land outline sampled every ~0.5 km. Each point
    belongs to the nearest zone centre if that centre is within
    ZONE_COAST_RADIUS_KM.
    """
    from scipy.spatial import cKDTree

    land = _land()
    pts = []
    for poly in getattr(land, "geoms", [land]):
        ring = poly.exterior.segmentize(0.005)  # ~0.5 km
        pts.extend(ring.coords)
    pts = np.array(pts)  # (n, 2) lon, lat
    xy = _to_km(pts[:, 1], pts[:, 0])

    centres = _to_km([z[1] for z in zone_key], [z[2] for z in zone_key])
    d = np.linalg.norm(xy[:, None, :] - centres[None, :, :], axis=2)
    owner = np.argmin(d, axis=1)
    owner[d[np.arange(len(owner)), owner] > config.ZONE_COAST_RADIUS_KM] = -1

    # Only Dominican coast belongs to a zone: Haiti's coast near Monte Cristi
    # or Pedernales still stops particles, but is not a DR beach.
    try:
        import shapely
        from shapely.geometry import shape

        with open(_DR_PATH, encoding="utf-8") as fh:
            dr = shape(json.load(fh)["geometry"]).buffer(0.02)  # ~2 km slack
        shapely.prepare(dr)
        owner[~shapely.contains_xy(dr, pts[:, 0], pts[:, 1])] = -1
    except Exception:
        logger.warning("drift: DR outline unavailable; all coast near a zone counts.", exc_info=True)
    return cKDTree(xy), owner


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def _seed(patch: dict, n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """n (lat, lon) release points spread over the patch.

    Uniform inside the outline when there is one; otherwise a Gaussian disc
    with the radius of a circle of the patch's area.
    """
    lat0, lon0 = float(patch["centroid_lat"]), float(patch["centroid_lon"])
    wkt = patch.get("geom_wkt")
    if wkt and n > 1:
        try:
            import shapely
            from shapely import wkt as _wkt

            poly = _wkt.loads(wkt)
            minx, miny, maxx, maxy = poly.bounds
            shapely.prepare(poly)
            lats, lons = [], []
            for _ in range(20):
                x = rng.uniform(minx, maxx, 4 * n)
                y = rng.uniform(miny, maxy, 4 * n)
                ok = shapely.contains_xy(poly, x, y)
                lons.extend(x[ok])
                lats.extend(y[ok])
                if len(lats) >= n:
                    return np.array(lats[:n]), np.array(lons[:n])
        except Exception:
            logger.debug("drift: could not seed inside outline; using a disc.", exc_info=True)
    if n == 1:
        return np.array([lat0]), np.array([lon0])
    r_km = math.sqrt(max(float(patch.get("area_km2", 0.0)), 0.0) / math.pi)
    return (lat0 + rng.normal(0, r_km / _KM_Y, n) if r_km else np.full(n, lat0),
            lon0 + rng.normal(0, r_km / _KM_X, n) if r_km else np.full(n, lon0))


def _risk_area(patch: dict) -> float:
    """The patch area the risk thresholds apply to (config.RISK_AREA_FIELD)."""
    for key in (config.RISK_AREA_FIELD, "area_km2"):
        try:
            v = float(patch.get(key))
            if v == v:  # not NaN
                return v
        except (TypeError, ValueError):
            continue
    return 0.0


def _observed_ts(patch: dict, now: dt.datetime) -> float:
    """Observation time of a patch (epoch s), clamped to [now - max age, now]."""
    raw = patch.get("observed_at")
    ts = now.timestamp()
    if raw:
        try:
            t = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            ts = t.timestamp()
        except ValueError:
            pass
    return min(ts, now.timestamp())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_particles(
    patches: list[dict],
    field,
    now: dt.datetime | None = None,
    horizon_hours: int = 72,
    n_particles: int | None = None,
    seed: int | None = None,
) -> dict:
    """Advect every patch's particles; return raw arrivals.

    Returns {"zone_names", "area" (P,), "member" (P*N,), "patch" (P*N,),
    "zone" (P*N,) int or -1, "hour" (P*N,) arrival hour from now (0 = already
    there; NaN = no arrival), "windage" (N,), "lat", "lon" (final positions)}.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    n = max(1, int(n_particles or config.DRIFT_PARTICLES))
    rng = np.random.default_rng(config.DRIFT_SEED if seed is None else seed)
    zones = config.ZONES
    zone_key = tuple((z["name"], float(z["center_lat"]), float(z["center_lon"])) for z in zones)
    tree, owner = _coast(zone_key)
    land = _land()

    max_age = config.DRIFT_MAX_AGE_DAYS * 86400
    kept = [p for p in patches if now.timestamp() - _observed_ts(p, now) <= max_age]
    if len(kept) < len(patches):
        logger.info("drift: dropped %d patch(es) older than %.0f days.",
                    len(patches) - len(kept), config.DRIFT_MAX_AGE_DAYS)

    windage = (np.full(n, config.WIND_DRIFT_FACTOR) if n == 1
               else rng.uniform(config.WIND_DRIFT_MIN, config.WIND_DRIFT_MAX, n))
    lat_l, lon_l, t0_l = [], [], []
    for p in kept:
        la, lo = _seed(p, n, rng)
        lat_l.append(la)
        lon_l.append(lo)
        t0_l.append(np.full(n, _observed_ts(p, now)))
    total = len(kept) * n
    out = {
        "zone_names": [z["name"] for z in zones],
        "area": np.array([_risk_area(p) for p in kept]),
        "member": np.tile(np.arange(n), len(kept)),
        "patch": np.repeat(np.arange(len(kept)), n),
        "zone": np.full(total, -1, dtype=int),
        "hour": np.full(total, np.nan),
        "windage": windage,
        "kept": kept,  # the patches actually released (for re-summarising)
    }
    if not total:
        out["lat"] = out["lon"] = np.array([])
        return out

    lat = np.concatenate(lat_l)
    lon = np.concatenate(lon_l)
    t0 = np.concatenate(t0_l)
    wk = windage[out["member"]]
    done = np.zeros(total, dtype=bool)
    now_s = now.timestamp()
    sigma_m = math.sqrt(2 * config.DRIFT_DIFFUSIVITY_M2S * 3600.0)

    present_s = config.DRIFT_PRESENT_HOURS * 3600.0

    def _check_arrival(t_s, idx: np.ndarray) -> None:
        """Stop particles at the coast. t_s: arrival time (scalar or per particle).

        Arrivals in the last DRIFT_PRESENT_HOURS count as present now (hour
        0); older ones stop the particle but are not counted, because
        sargassum that beached days ago is no longer the forecast's concern.
        """
        if not len(idx):
            return
        import shapely
        dist, nearest = tree.query(_to_km(lat[idx], lon[idx]))
        hit = (dist <= config.ARRIVAL_KM) | shapely.contains_xy(land, lon[idx], lat[idx])
        if hit.any():
            j = idx[hit]
            when = (np.broadcast_to(np.asarray(t_s, dtype=float), idx.shape)[hit] - now_s)
            done[j] = True
            recent = when >= -present_s
            out["zone"][j] = np.where(recent, owner[nearest[hit]], -1)
            out["hour"][j] = np.where(recent, np.maximum(0.0, when / 3600.0), np.nan)

    # Particles already at the coast when their patch was observed.
    _check_arrival(t0, np.arange(total))

    t = float(np.floor(t0.min() / 3600.0) * 3600.0)
    end = now_s + horizon_hours * 3600.0

    # Snapshots of every particle at now, +24 h, +48 h… for the map's tracks.
    snap_hours = [h for h in TRACK_HOURS if h <= horizon_hours]
    pending = list(snap_hours)
    snaps: dict[int, tuple] = {}

    def _take_snapshots(t_now: float) -> None:
        while pending and t_now >= now_s + pending[0] * 3600.0:
            h = pending.pop(0)
            snaps[h] = (lat.copy(), lon.copy(), done.copy())

    _take_snapshots(t)
    while t < end:
        active = np.nonzero(~done & (t0 <= t))[0]
        if len(active):
            uc, vc = field.current(t, lat[active], lon[active])
            uw, vw = field.wind(t, lat[active], lon[active])
            ue = uc + wk[active] * uw
            ve = vc + wk[active] * vw
            dx = ue * 3600.0 + rng.normal(0, sigma_m, len(active))
            dy = ve * 3600.0 + rng.normal(0, sigma_m, len(active))
            lat[active] += dy / _METERS_PER_DEG
            lon[active] += dx / (np.cos(np.radians(lat[active])) * _METERS_PER_DEG)
            _check_arrival(t + 3600.0, active)
        t += 3600.0
        _take_snapshots(t)

    out["lat"], out["lon"] = lat, lon
    out["snapshots"] = snaps
    return out


TRACK_HOURS = (0, 24, 48, 72)


def patch_tracks(raw: dict) -> list[list[dict]]:
    """Per released patch: where its particles are at now, +24 h, +48 h, +72 h.

    Each point is the median particle position (particles that reached the
    coast stay where they stopped) and `ashore`, the share of particles that
    have reached the coast by then. Aligned with raw["kept"].
    """
    snaps = raw.get("snapshots") or {}
    n_patches = len(raw["kept"])
    tracks: list[list[dict]] = [[] for _ in range(n_patches)]
    if not snaps or not n_patches:
        return tracks
    patch_idx = raw["patch"]
    for h in sorted(snaps):
        lat, lon, done = snaps[h]
        for i in range(n_patches):
            sel = patch_idx == i
            tracks[i].append({
                "h": int(h),
                "lat": round(float(np.median(lat[sel])), 4),
                "lon": round(float(np.median(lon[sel])), 4),
                "ashore": round(float(done[sel].mean()), 2),
            })
    return tracks


def beach_exposures(field, beaches: list[dict], now: dt.datetime, hours: int = 24) -> dict[str, float | None]:
    """How squarely the next `hours` of real drift hit each beach's face.

    Mean of current + central windage × wind over the period, at a point
    5 km offshore along the beach's seaward direction, compared with that
    direction (dashboard.risk_overlay.shore_exposure: +1 onto the beach,
    -1 away). Uses the same gridded fields as the forecast, so the card's
    "pushes toward / away from this shore" line can't contradict it.
    """
    from dashboard.risk_overlay import shore_exposure
    from dashboard.surf_data import profile_for_beach

    times = [now.timestamp() + 3600.0 * k for k in range(0, hours + 1, 3)]
    out: dict[str, float | None] = {}
    for b in beaches:
        try:
            normal = float(profile_for_beach(b)["shore_normal_deg"])
            lat0, lon0 = float(b["latitude"]), float(b["longitude"])
            lat = lat0 + (5.0 / _KM_Y) * math.cos(math.radians(normal))
            lon = lon0 + (5.0 / (111.32 * math.cos(math.radians(lat0)))) * math.sin(math.radians(normal))
            us, vs = [], []
            for t in times:
                uc, vc = field.current(t, lat, lon)
                uw, vw = field.wind(t, lat, lon)
                us.append(float(uc[0] + config.WIND_DRIFT_FACTOR * uw[0]))
                vs.append(float(vc[0] + config.WIND_DRIFT_FACTOR * vw[0]))
            e = shore_exposure(normal, float(np.mean(us)), float(np.mean(vs)))
            out[b["name"]] = None if e is None else round(e, 3)
        except Exception:
            out[b["name"]] = None
    return out


def _summarise_selection(raw: dict, sel: np.ndarray, now: dt.datetime, hours: list[int],
                         min_km2: Optional[float] = None) -> dict:
    """Risk, probabilities, ETA and horizons for the arrivals selected by `sel`.

    Shared by zones and beaches so both follow the same rules; `min_km2`
    (default RISK_AREA_MIN_KM2) is the only difference, beaches use a lower bar.
    """
    min_km2 = config.RISK_AREA_MIN_KM2 if min_km2 is None else min_km2
    horizons = sorted(set(hours))
    max_h = max(horizons)
    n = len(raw["windage"])
    area = raw["area"]
    sel = sel & ~np.isnan(raw["hour"])
    per_member: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for m, pidx, h in zip(raw["member"][sel], raw["patch"][sel], raw["hour"][sel]):
        per_member[m].append((int(math.ceil(h)), float(area[pidx])))

    def levels_at(h: int) -> list[str]:
        return [
            _classify_risk(sum(a for hr, a in arr if hr <= h),
                           _significant_eta(arr, h, config.RISK_AREA_LOW_MAX_KM2),
                           min_km2)
            for arr in per_member
        ]

    def headline(levels: list[str]) -> tuple[str, dict]:
        probs = {lv: levels.count(lv) / n for lv in _LEVELS}
        level = "none"
        for k, lv in enumerate(_LEVELS):
            if sum(probs[x] for x in _LEVELS[k:]) >= config.RISK_PROB_THRESHOLD:
                level = lv
        return level, {k: round(v, 3) for k, v in probs.items()}

    horizon_breakdown = []
    for h in horizons:
        level, probs = headline(levels_at(h))
        horizon_breakdown.append({
            "horizon_hours": h,
            "risk_level": level,
            "area_km2": round(sum(a for arr in per_member for hr, a in arr if hr <= h) / n, 3),
            "valid_at": (now + dt.timedelta(hours=h)).isoformat(),
            "probs": probs,
        })

    summary_level, summary_probs = headline(levels_at(max_h))
    # ETA: per member, when the arrived area first becomes enough to count as
    # risk (min_km2) — the same bar as arrival_probability, so a
    # trace never produces an ETA for a zone with 0 % arrival probability.
    first = [t for t in (_significant_eta(arr, max_h, min_km2)
                         for arr in per_member) if t is not None]
    eta_h = int(np.median(first)) if len(first) / n >= config.RISK_PROB_THRESHOLD else None
    return {
        "risk_level": summary_level,
        "eta_hours": eta_h,
        "eta_timestamp": (now + dt.timedelta(hours=eta_h)).isoformat() if eta_h is not None else None,
        "arrival_probability": round(1.0 - summary_probs["none"], 3),
        "risk_probs": summary_probs,
        "horizons": horizon_breakdown,
    }


def summarise(raw: dict, now: dt.datetime, hours: list[int]) -> list[dict]:
    """Turn particle arrivals into one forecast dict per zone."""
    results: list[dict] = []
    for zi, name in enumerate(raw["zone_names"]):
        res = {"zone_id": zi + 1, "zone_name": name,
               **_summarise_selection(raw, raw["zone"] == zi, now, hours)}
        results.append(res)
        logger.info("Zone %s: risk=%s p(arrival)=%.2f eta=%sh probs=%s", name, res["risk_level"],
                    res["arrival_probability"], res["eta_hours"], res["risk_probs"])
    return results


def summarise_beaches(raw: dict, beaches: list[dict], now: dt.datetime,
                      hours: list[int] | None = None) -> list[dict]:
    """One forecast per beach: particles that STOPPED within BEACH_RADIUS_KM.

    Uses the zone thresholds except the minimum area, which is
    BEACH_RISK_AREA_MIN_KM2: the area reaching one beach is much smaller than
    a whole zone's. Tune against visitor reports (pipeline/replay.py).
    """
    hours = hours or [0, 24, 48, 72]
    if not len(raw["hour"]):
        stops = np.zeros((0, 2))
    else:
        stops = _to_km(raw["lat"], raw["lon"])
    zone_names = raw["zone_names"]
    out = []
    for b in beaches:
        here = _to_km([float(b["latitude"])], [float(b["longitude"])])[0]
        near = (np.linalg.norm(stops - here, axis=1) <= config.BEACH_RADIUS_KM
                if len(stops) else np.zeros(0, dtype=bool))
        res = _summarise_selection(raw, near, now, hours, config.BEACH_RISK_AREA_MIN_KM2)
        zone_idx = raw["zone"][near & ~np.isnan(raw["hour"])]
        zone_idx = zone_idx[zone_idx >= 0]
        res.update({
            "beach_name": b["name"],
            "zone_name": zone_names[int(np.bincount(zone_idx).argmax())] if len(zone_idx) else None,
        })
        out.append(res)
    return out


def project_drift(
    patches: list[dict],
    field,
    hours: list[int] | None = None,
    now: dt.datetime | None = None,
    n_particles: int | None = None,
    seed: int | None = None,
) -> list[dict]:
    """Forecast per-zone arrival risk for the next max(hours) hours.

    Args:
        patches: detection dicts with centroid_lat, centroid_lon, area_km2 and
                 optionally geom_wkt (outline) and observed_at (ISO time).
        field:   ocean.ForcingField (currents + wind, interpolated in place).
        hours:   forecast checkpoints (default [0, 24, 48, 72]).

    Returns one dict per zone: zone_id (1-based config index), zone_name,
    risk_level, eta_hours, eta_timestamp, arrival_probability, risk_probs,
    horizons [{horizon_hours, risk_level, area_km2, valid_at, probs}].
    """
    hours = hours or [0, 24, 48, 72]
    now = now or dt.datetime.now(dt.timezone.utc)
    raw = run_particles(patches, field, now=now, horizon_hours=max(hours),
                        n_particles=n_particles, seed=seed)
    arrived = int((~np.isnan(raw["hour"])).sum())
    logger.info("drift: %d patch(es) × %d particles, %d arrived at a coast (%d at a zone).",
                len(raw["area"]), len(raw["windage"]), arrived,
                int(((raw["zone"] >= 0) & ~np.isnan(raw["hour"])).sum()))
    return summarise(raw, now, hours)


def forecast_zones_and_beaches(
    patches: list[dict],
    field,
    beaches: list[dict],
    hours: list[int] | None = None,
    now: dt.datetime | None = None,
    n_particles: int | None = None,
    seed: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """Zone forecasts and per-beach forecasts from ONE particle run."""
    hours = hours or [0, 24, 48, 72]
    now = now or dt.datetime.now(dt.timezone.utc)
    raw = run_particles(patches, field, now=now, horizon_hours=max(hours),
                        n_particles=n_particles, seed=seed)
    logger.info("drift: %d patch(es) × %d particles, %d arrived at a coast.",
                len(raw["area"]), len(raw["windage"]), int((~np.isnan(raw["hour"])).sum()))
    # The map draws these instead of guessing with fixed currents. Patches
    # too old to release get no track.
    for patch, track in zip(raw["kept"], patch_tracks(raw)):
        patch["track"] = track
    beach_fcs = summarise_beaches(raw, beaches, now, hours)
    exposure = beach_exposures(field, beaches, now)
    for bf in beach_fcs:
        bf["exposure"] = exposure.get(bf["beach_name"])
    return summarise(raw, now, hours), beach_fcs


def zone_coast_wkt() -> dict[str, str]:
    """{zone name: MULTILINESTRING WKT} of the coast each zone owns.

    Same coast points and ownership the drift uses, joined into runs of
    consecutive points with one owner. Stored in zones.coast for maps.
    """
    from shapely.geometry import LineString, MultiLineString

    zones = config.ZONES
    zone_key = tuple((z["name"], float(z["center_lat"]), float(z["center_lon"])) for z in zones)
    _, owner = _coast(zone_key)
    land = _land()
    pts = []
    for poly in getattr(land, "geoms", [land]):
        ring = list(poly.exterior.segmentize(0.005).coords)
        pts.append(ring)
    lines: dict[int, list] = {}
    k = 0
    for ring in pts:
        run: list = []
        run_owner = None
        for xy in ring:
            o = int(owner[k])
            k += 1
            if o != run_owner:
                if run_owner is not None and run_owner >= 0 and len(run) > 1:
                    lines.setdefault(run_owner, []).append(LineString(run))
                run, run_owner = [], o
            run.append(xy)
        if run_owner is not None and run_owner >= 0 and len(run) > 1:
            lines.setdefault(run_owner, []).append(LineString(run))
    # ~100 m simplification keeps /zones small for the map.
    return {zones[i]["name"]: MultiLineString(ls).simplify(0.001).wkt for i, ls in lines.items()}
