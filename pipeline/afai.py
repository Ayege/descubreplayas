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

"""Independent check of the Sentinel-2 detections against NOAA/USF daily AFAI.

USF's Alternative Floating Algae Index (Hu et al.) comes from MODIS/VIIRS:
much coarser than Sentinel-2 (~1.6 km vs 10-60 m) but DAILY, where Sentinel-2
revisits every 5+ days. NOAA/AOML serves it free on ERDDAP. This module

1. downloads the last few daily fields over the DR EEZ,
2. flags cells that stand out from their local background,
3. groups flagged cells into clusters,
4. measures how far Sentinel-2 and AFAI agree (`compare`), and
5. returns the AFAI clusters Sentinel-2 has no patch near, so the pipeline
   can add them as detections when clouds hid them from Sentinel-2.

Agreement is not accuracy: both are satellite estimates. The beach reports
(api/main.py POST /reports) are the ground truth.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import certifi
import numpy as np
import requests
from scipy import ndimage as ndi
from shapely.geometry import MultiPoint

from pipeline import config

logger = logging.getLogger(__name__)

_EARTH_R_KM = 6371.0
_KM_PER_DEG = 111.32


@dataclass
class AfaiField:
    """One day of AFAI on a regular lat/lon grid (rows = lats ascending)."""
    date: dt.date
    lats: np.ndarray   # (n_lat,)
    lons: np.ndarray   # (n_lon,)
    values: np.ndarray  # (n_lat, n_lon), NaN where cloud/land/no data


# ---------------------------------------------------------------------------
# Download + parse
# ---------------------------------------------------------------------------

def _query(days: int, bbox: dict) -> str:
    """ERDDAP griddap query for the last `days` time steps over `bbox`.

    `last-N:1:last` is index arithmetic on the time axis, so it returns the N+1
    most recent fields whatever their dates (the axis has gaps).
    """
    n = max(1, days) - 1
    t = f"[last-{n}:1:last]" if n else "[last:1:last]"
    return (
        f"AFAI{t}"
        f"[({bbox['min_lat']}):1:({bbox['max_lat']})]"
        f"[({bbox['min_lon']}):1:({bbox['max_lon']})]"
    )


def parse_erddap_json(payload: dict) -> list[AfaiField]:
    """Turn an ERDDAP griddap .json table into one AfaiField per time step."""
    table = payload["table"]
    cols = table["columnNames"]
    it, ilat, ilon, iv = (cols.index(c) for c in ("time", "latitude", "longitude", "AFAI"))
    rows = table["rows"]
    if not rows:
        return []

    times = np.array([r[it] for r in rows])
    lat = np.array([r[ilat] for r in rows], dtype=float)
    lon = np.array([r[ilon] for r in rows], dtype=float)
    val = np.array([np.nan if r[iv] is None else r[iv] for r in rows], dtype=float)

    lats = np.unique(lat)
    lons = np.unique(lon)
    yi = np.searchsorted(lats, lat)
    xi = np.searchsorted(lons, lon)

    fields: list[AfaiField] = []
    for t in sorted(set(times)):
        sel = times == t
        grid = np.full((len(lats), len(lons)), np.nan)
        grid[yi[sel], xi[sel]] = val[sel]
        day = dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")).date()
        fields.append(AfaiField(day, lats, lons, grid))
    return fields


def fetch_fields(days: int | None = None, bbox: dict | None = None) -> list[AfaiField]:
    """Download the most recent `days` daily AFAI fields over the EEZ."""
    days = days or config.AFAI_DAYS
    bbox = bbox or config.EEZ_BBOX
    url = f"{config.AFAI_ERDDAP_URL}?{_query(days, bbox)}"
    resp = requests.get(url, timeout=180, verify=certifi.where())
    resp.raise_for_status()
    fields = parse_erddap_json(resp.json())
    logger.info("afai: fetched %d field(s): %s", len(fields), [f.date.isoformat() for f in fields])
    return fields


# ---------------------------------------------------------------------------
# Flagging
# ---------------------------------------------------------------------------

def flag_field(values: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (flagged, anomaly, clear) masks for one field.

    clear   : the satellite had a usable view (not NaN, not saturated, and
              not within AFAI_EDGE_BUFFER_CELLS of a cloud or the coast).
    anomaly : AFAI minus the local median background, which removes the
              smooth haze/glint gradient a fixed threshold can't.
    flagged : clear and anomaly > AFAI_ANOMALY_THRESHOLD.
    """
    nan = np.isnan(values)
    if nan.all():
        empty = np.zeros_like(values, dtype=bool)
        return empty, np.zeros_like(values), empty

    edge = (
        ndi.binary_dilation(nan, iterations=config.AFAI_EDGE_BUFFER_CELLS)
        if config.AFAI_EDGE_BUFFER_CELLS > 0 else nan
    )
    saturated = np.where(nan, False, values >= config.AFAI_SATURATED)
    filled = np.where(nan, np.nanmedian(values), values)
    background = ndi.median_filter(filled, size=config.AFAI_BG_WINDOW)
    anomaly = np.where(nan, 0.0, values - background)

    clear = ~nan & ~edge & ~saturated
    flagged = clear & (anomaly > config.AFAI_ANOMALY_THRESHOLD)
    return flagged, anomaly, clear


def combine_latest(fields: list[AfaiField]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Merge fields, keeping each cell's MOST RECENT clear view.

    Same idea as the Sentinel-2 mosaic in detect.py: a moving mass should be
    shown where it was last seen, not averaged over several positions.
    Returns (flagged, anomaly, clear_any, day_index) where day_index is the
    index into `fields` of the view used (-1 = never clear).
    """
    ordered = sorted(fields, key=lambda f: f.date)
    shape = ordered[0].values.shape
    flagged = np.zeros(shape, dtype=bool)
    anomaly = np.zeros(shape)
    day_index = np.full(shape, -1, dtype=int)
    for i, f in enumerate(ordered):
        fl, an, cl = flag_field(f.values)
        flagged[cl] = fl[cl]
        anomaly[cl] = an[cl]
        day_index[cl] = i
    return flagged, anomaly, day_index >= 0, day_index


def clusters(
    flagged: np.ndarray,
    anomaly: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    dates: list[dt.date] | None = None,
    day_index: np.ndarray | None = None,
) -> list[dict]:
    """Group flagged cells (8-connected) into patch dicts like detect.py's.

    area_km2 is the sargassum-COVERED area: each cell's area times the share
    of it covered (anomaly / AFAI_FULL_COVER). Sentinel-2's area_km2 is the
    OUTLINE area of flagged 60 m pixels, which is larger for the same mass,
    so a gap-filled AFAI patch reads as a smaller risk than a Sentinel-2 patch
    of the same mass. That is deliberately conservative.
    """
    labels, n = ndi.label(flagged, structure=np.ones((3, 3), dtype=int))
    if n == 0:
        return []

    dlat = float(np.median(np.diff(lats))) if len(lats) > 1 else 0.015
    dlon = float(np.median(np.diff(lons))) if len(lons) > 1 else 0.015
    out: list[dict] = []
    for k in range(1, n + 1):
        ys, xs = np.nonzero(labels == k)
        if len(ys) < config.AFAI_MIN_CLUSTER_CELLS:
            continue
        clat = lats[ys]
        clon = lons[xs]
        cover = np.clip(anomaly[ys, xs] / config.AFAI_FULL_COVER, 0.0, 1.0)
        cell_km2 = (dlat * _KM_PER_DEG) * (dlon * _KM_PER_DEG * np.cos(np.radians(clat)))
        corners = [
            (lo + sx * dlon / 2, la + sy * dlat / 2)
            for la, lo in zip(clat, clon)
            for sx in (-1, 1) for sy in (-1, 1)
        ]
        patch = {
            "centroid_lat": float(clat.mean()),
            "centroid_lon": float(clon.mean()),
            "area_km2": float((cell_km2 * cover).sum()),
            "geom_wkt": MultiPoint(corners).convex_hull.wkt,
            "source": config.AFAI_SOURCE,
            "n_cells": int(len(ys)),
        }
        if dates is not None and day_index is not None:
            seen = day_index[ys, xs]
            seen = seen[seen >= 0]
            if len(seen):
                patch["observed_on"] = dates[int(seen.max())].isoformat()
        out.append(patch)
    return out


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _haversine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise great-circle distance (km) between (lat, lon) rows of a and b."""
    la1, lo1 = np.radians(a[:, 0])[:, None], np.radians(a[:, 1])[:, None]
    la2, lo2 = np.radians(b[:, 0])[None, :], np.radians(b[:, 1])[None, :]
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * _EARTH_R_KM * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))


def _points(patches: list[dict]) -> np.ndarray:
    return np.array(
        [[float(p["centroid_lat"]), float(p["centroid_lon"])] for p in patches], dtype=float
    ).reshape(-1, 2)


def _seen_clear(points: np.ndarray, clear: np.ndarray, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """True for each point whose AFAI cell (or a direct neighbour) had a clear view."""
    out = np.zeros(len(points), dtype=bool)
    if not len(points):
        return out
    yi = np.clip(np.searchsorted(lats, points[:, 0]), 0, len(lats) - 1)
    xi = np.clip(np.searchsorted(lons, points[:, 1]), 0, len(lons) - 1)
    near = ndi.binary_dilation(clear, iterations=1)
    inside = (
        (points[:, 0] >= lats[0]) & (points[:, 0] <= lats[-1])
        & (points[:, 1] >= lons[0]) & (points[:, 1] <= lons[-1])
    )
    out[:] = near[yi, xi] & inside
    return out


def compare(
    s2_patches: list[dict],
    afai_patches: list[dict],
    clear: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    match_km: float | None = None,
) -> tuple[dict, list[dict]]:
    """Measure agreement and pick AFAI clusters Sentinel-2 missed.

    Returns (summary, unmatched_afai) where summary has:
      s2_patches    Sentinel-2 patches this run
      s2_checkable  of those, how many AFAI had a clear view of
      s2_confirmed  of the checkable ones, how many have an AFAI cluster nearby
      afai_clusters AFAI clusters this run
      afai_matched  of those, how many have a Sentinel-2 patch nearby
    `s2_confirmed / s2_checkable` is a rough precision check on Sentinel-2;
    `afai_matched / afai_clusters` shows how much AFAI sees that S2 doesn't.
    """
    match_km = config.AFAI_MATCH_KM if match_km is None else match_km
    s2 = _points(s2_patches)
    af = _points(afai_patches)

    if len(s2) and len(af):
        d = _haversine_matrix(s2, af)
        s2_has_afai = d.min(axis=1) <= match_km
        afai_has_s2 = d.min(axis=0) <= match_km
    else:
        s2_has_afai = np.zeros(len(s2), dtype=bool)
        afai_has_s2 = np.zeros(len(af), dtype=bool)

    checkable = _seen_clear(s2, clear, lats, lons)
    summary = {
        "s2_patches": int(len(s2)),
        "s2_checkable": int(checkable.sum()),
        "s2_confirmed": int((checkable & s2_has_afai).sum()),
        "afai_clusters": int(len(af)),
        "afai_matched": int(afai_has_s2.sum()),
    }
    unmatched = [p for p, m in zip(afai_patches, afai_has_s2) if not m]
    return summary, unmatched


def check_and_fill(s2_patches: list[dict]) -> tuple[list[dict], dict]:
    """Pipeline entry point: compare against AFAI and return gap-fill patches.

    Returns (gap_patches, summary). gap_patches is empty when AFAI_GAP_FILL is
    off. summary is ready for store.insert_detection_check().
    """
    fields = fetch_fields()
    if not fields:
        return [], {"afai_dates": [], "afai_clear_frac": 0.0}

    ordered = sorted(fields, key=lambda f: f.date)
    lats, lons = ordered[0].lats, ordered[0].lons
    flagged, anomaly, clear, day_index = combine_latest(ordered)
    afai_patches = clusters(
        flagged, anomaly, lats, lons, [f.date for f in ordered], day_index
    )
    summary, unmatched = compare(s2_patches, afai_patches, clear, lats, lons)

    gap = unmatched if config.AFAI_GAP_FILL else []
    summary.update({
        "afai_dates": [f.date.isoformat() for f in ordered],
        "afai_clear_frac": round(float(clear.mean()), 4),
        "gap_filled": len(gap),
        "gap_filled_area_km2": round(sum(p["area_km2"] for p in gap), 4),
    })
    logger.info("afai: %s", summary)
    return gap, summary
