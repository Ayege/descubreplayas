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

"""Feature engineering for the extended sargassum forecast model.

Converts raw detection aggregates + zone metadata into a flat numeric vector
suitable for scikit-learn classifiers.  All features are bounded and logged
so the model is robust to outliers in detection area.

Feature vector layout (FEATURE_NAMES defines the order):
  doy_sin / doy_cos     – day-of-year encoded cyclically (captures seasonality)
  month_sin / month_cos – month encoded cyclically (coarser seasonal signal)
  log_eez_area          – log1p total detected sargassum area in the DR EEZ (km²)
  log_zone_area         – log1p estimated area within 200 km of this zone (km²)
  log_nearest_km        – log1p distance (km) from zone centre to nearest mass
  log_patch_count       – log1p number of distinct patches in the EEZ
  physics_risk          – 0-3 integer from the 72-h drift forecast for this zone
  zone_lat / zone_lon   – zone centre (lets model learn coast-specific behaviour)
  lead_days             – forecast horizon in days (7, 14, or 21)
"""
from __future__ import annotations

import math
from typing import Sequence


FEATURE_NAMES: list[str] = [
    "doy_sin", "doy_cos",
    "month_sin", "month_cos",
    "log_eez_area",
    "log_zone_area",
    "log_nearest_km",
    "log_patch_count",
    "physics_risk",
    "zone_lat",
    "zone_lon",
    "lead_days",
]

RISK_TO_INT: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}
INT_TO_RISK: dict[int, str] = {v: k for k, v in RISK_TO_INT.items()}


def _cyclic(value: float, period: float) -> tuple[float, float]:
    angle = 2 * math.pi * value / period
    return math.sin(angle), math.cos(angle)


def build_feature_vector(
    *,
    day_of_year: int,
    month: int,
    eez_area_km2: float,
    zone_area_km2: float,
    nearest_km: float,
    patch_count: int,
    physics_risk: int,           # 0-3 from drift.py classification
    zone_lat: float,
    zone_lon: float,
    lead_days: int,
) -> list[float]:
    """Return a fixed-length float vector for one (zone, date, lead) triple."""
    doy_sin, doy_cos = _cyclic(day_of_year, 365)
    mon_sin, mon_cos = _cyclic(month, 12)
    return [
        doy_sin, doy_cos,
        mon_sin, mon_cos,
        math.log1p(max(0.0, eez_area_km2)),
        math.log1p(max(0.0, zone_area_km2)),
        math.log1p(max(0.0, nearest_km)),
        math.log1p(max(0, patch_count)),
        float(physics_risk),
        zone_lat,
        zone_lon,
        float(lead_days),
    ]


def detection_state_for_zone(
    zone: dict,
    detections: Sequence[dict],
    zone_radius_km: float = 200.0,
) -> tuple[float, float, float, int]:
    """Aggregate raw detections into per-zone summary stats.

    Returns (eez_area_km2, zone_area_km2, nearest_km, patch_count).
    Uses a fast O(n) haversine approximation — fine for the <1000 patches expected.
    """
    import math as _m

    zlat, zlon = float(zone["center_lat"]), float(zone["center_lon"])
    eez_area = zone_area = 0.0
    nearest = math.inf
    count = 0

    for d in detections:
        try:
            dlat, dlon = float(d.get("centroid_lat") or d.get("lat", 0)), \
                         float(d.get("centroid_lon") or d.get("lon", 0))
            area = float(d.get("area_km2") or 0.0)
        except (TypeError, ValueError):
            continue

        # Haversine
        r = 6371.0
        p1, p2 = _m.radians(zlat), _m.radians(dlat)
        a = (_m.sin((dlat - zlat) * _m.pi / 180 / 2) ** 2
             + _m.cos(p1) * _m.cos(p2)
             * _m.sin((dlon - zlon) * _m.pi / 180 / 2) ** 2)
        dist = 2 * r * _m.asin(_m.sqrt(a))

        eez_area += area
        count += 1
        if dist < nearest:
            nearest = dist
        if dist <= zone_radius_km:
            zone_area += area

    if nearest == math.inf:
        nearest = 999.0  # sentinel: no detections → encode as very far

    return eez_area, zone_area, nearest, count
