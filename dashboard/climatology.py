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

"""Seasonal sargassum climatology model for the Dominican Republic.

WHY THIS EXISTS
---------------
The physics drift forecast (currents + wind) is only skilful for ~72 hours.
For any date further out, trajectories diverge and a deterministic forecast is
meaningless. The established way to answer "will there be sargassum in July?"
is **climatology**: the Caribbean sargassum bloom follows a strong, repeatable
annual cycle (very low in Jan–Feb, ramping Mar–Apr, peaking Jun–Aug, declining
Sep–Nov). This module encodes that cycle as a 0..1 monthly abundance index,
modulated by how exposed each DR coast is to the open-Atlantic sargassum belt.

This is NOT a trained ML model — it is a transparent reference climatology
derived from the published satellite-era seasonal pattern (e.g. USF Sargassum
Watch System / NOAA AOML monthly bulletins). It gives a defensible *expected*
risk for far-future dates, clearly labelled as a seasonal estimate rather than
a deterministic forecast.

USAGE
-----
    from dashboard.climatology import seasonal_risk, seasonal_index
    risk = seasonal_risk(month=7, region="East (Punta Cana / La Romana)")
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Caribbean basin monthly abundance index (0..1), relative to the annual peak.
# Shape follows the well-documented Great Atlantic Sargassum Belt seasonality:
# minimum in boreal winter, sharp spring ramp, summer maximum, autumn decline.
# ---------------------------------------------------------------------------
MONTHLY_INDEX: dict[int, float] = {
    1: 0.12,   # January  — basin minimum
    2: 0.15,   # February
    3: 0.30,   # March    — spring ramp begins
    4: 0.50,   # April
    5: 0.72,   # May
    6: 0.90,   # June
    7: 1.00,   # July     — annual peak
    8: 0.88,   # August
    9: 0.60,   # September — decline
    10: 0.38,  # October
    11: 0.22,  # November
    12: 0.13,  # December
}

# ---------------------------------------------------------------------------
# Per-region exposure multiplier (0..1). Atlantic-facing east/north coasts sit
# directly in the path of the sargassum belt and are hit hardest; the Caribbean
# (southern) shoreline is more sheltered but still receives periodic influxes.
# Keys must match the region strings in dashboard.beaches_data.
# ---------------------------------------------------------------------------
REGION_EXPOSURE: dict[str, float] = {
    "East (Punta Cana / La Romana)": 1.00,    # full Atlantic exposure
    "North (Puerto Plata / Cabarete)": 0.90,  # Atlantic north shore
    "Samaná Peninsula": 0.70,                 # bay, partially sheltered
    "South (Santo Domingo / South Coast)": 0.50,   # Caribbean side
    "Southwest (Barahona / Pedernales)": 0.50,     # Caribbean side
}

# Default exposure when a region is unknown (treat as moderately exposed).
_DEFAULT_EXPOSURE = 0.7

# ---------------------------------------------------------------------------
# Risk thresholds on the combined index (monthly * regional exposure).
# Tuned so a peak-season Atlantic coast reads 'high' and a winter Caribbean
# coast reads 'none', matching lived experience on DR beaches.
# ---------------------------------------------------------------------------
_RISK_BANDS = (
    (0.15, "none"),
    (0.35, "low"),
    (0.60, "medium"),
    (1.01, "high"),
)


def seasonal_index(month: int, region: str | None = None) -> float:
    """Return the expected sargassum abundance index (0..1) for a month/region.

    `month` is 1-12. `region` is an optional region string; when provided the
    basin index is scaled by that coast's exposure multiplier.
    """
    base = MONTHLY_INDEX.get(int(month), 0.0)
    if region is None:
        return base
    exposure = REGION_EXPOSURE.get(region, _DEFAULT_EXPOSURE)
    return base * exposure


def seasonal_risk(month: int, region: str | None = None) -> str:
    """Return a risk level ('none'|'low'|'medium'|'high') for a month/region."""
    idx = seasonal_index(month, region)
    for threshold, level in _RISK_BANDS:
        if idx < threshold:
            return level
    return "high"


def index_to_risk(idx: float) -> str:
    """Map a raw 0..1 abundance index to a risk level."""
    for threshold, level in _RISK_BANDS:
        if idx < threshold:
            return level
    return "high"
