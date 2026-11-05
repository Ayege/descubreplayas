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

"""Offline test for the live-risk overlay nearest-zone logic.

Run from repo root:
    python -m tests.test_risk_overlay
"""
from __future__ import annotations

import sys

from dashboard.beaches_data import beaches_with_maps
from dashboard.risk_overlay import (
    RISK_COLORS,
    haversine_km,
    nearest_zone,
    risk_for_beach,
)

# The 5 monitored zones (matches sql/schema.sql seed + pipeline/config.py).
ZONES = [
    {"id": 1, "name": "Punta Cana", "center_lat": 18.58, "center_lon": -68.37},
    {"id": 2, "name": "Bavaro", "center_lat": 18.68, "center_lon": -68.43},
    {"id": 3, "name": "Samana", "center_lat": 19.20, "center_lon": -69.33},
    {"id": 4, "name": "Puerto Plata", "center_lat": 19.80, "center_lon": -70.69},
    {"id": 5, "name": "Juan Dolio", "center_lat": 18.43, "center_lon": -69.42},
]


def test_haversine_known_distance() -> None:
    # Punta Cana zone to Bavaro zone ~ 12-13 km.
    d = haversine_km(18.58, -68.37, 18.68, -68.43)
    assert 8 < d < 20, f"Unexpected distance: {d:.1f} km"
    print(f"  haversine Punta Cana→Bavaro = {d:.1f} km: OK")


def test_nearest_zone_matches_geography() -> None:
    beaches = {b["name"]: b for b in beaches_with_maps()}

    cases = {
        "Playa Bávaro": {"Punta Cana", "Bavaro"},
        "Playa Dorada": {"Puerto Plata"},
        "Playa Rincón": {"Samana"},
        "Boca Chica Beach": {"Juan Dolio"},
        "Playa Juan Dolio": {"Juan Dolio"},
    }
    for beach_name, expected in cases.items():
        beach = beaches[beach_name]
        z = nearest_zone(beach["latitude"], beach["longitude"], ZONES)
        assert z is not None and z["name"] in expected, (
            f"{beach_name}: expected nearest in {expected}, got {z['name'] if z else None}"
        )
        print(f"  {beach_name} → {z['name']}: OK")


def test_risk_mapping_and_fallback() -> None:
    beaches = {b["name"]: b for b in beaches_with_maps()}
    bavaro = beaches["Playa Bávaro"]

    # High risk at Punta Cana (id=1) should propagate to nearby Bávaro.
    risk, zone, _dist, fc = risk_for_beach(bavaro, ZONES, {1: {"risk_level": "high"}})
    assert risk in {"high", "none"}, f"Unexpected risk: {risk}"
    if zone["id"] == 1:
        assert risk == "high", "Bávaro nearest Punta Cana should read high"
        assert fc == {"risk_level": "high"}, "The forecast for the zone should come back"
    assert risk in RISK_COLORS, "Risk must be a known colour key"

    # Missing forecast for the nearest zone falls back to 'none'.
    risk_none, _z, _d, no_forecast = risk_for_beach(bavaro, ZONES, {})
    assert risk_none == "none", f"Expected fallback 'none', got {risk_none}"
    assert no_forecast is None, "No forecast should come back"
    print("  risk mapping + fallback: OK")


def test_shore_exposure_and_adjustment() -> None:
    from dashboard.risk_overlay import adjust_for_exposure, shore_exposure
    # East-facing beach (90°), drift heading west (u<0): pushed straight on.
    assert abs(shore_exposure(90, -0.3, 0.0) - 1.0) < 1e-9
    # Same beach, drift heading east: carried straight off.
    assert abs(shore_exposure(90, 0.3, 0.0) + 1.0) < 1e-9
    # North-facing beach, drift heading west: along the shore.
    assert abs(shore_exposure(0, -0.3, 0.0)) < 1e-9
    assert shore_exposure(0, 0.001, 0.0) is None          # too weak to have a direction
    assert adjust_for_exposure("high", -0.9) == "medium"
    assert adjust_for_exposure("medium", -0.9) == "low"
    assert adjust_for_exposure("low", -0.9) == "low"      # never below 'low'
    assert adjust_for_exposure("none", -0.9) == "none"
    assert adjust_for_exposure("high", 0.0) == "high"     # along-shore: unchanged
    assert adjust_for_exposure("high", None) == "high"
    assert adjust_for_exposure("out", -0.9) == "out"
    print("  exposure from shore orientation and drift: OK")


def test_track_position() -> None:
    from dashboard.risk_overlay import track_position
    mass = {"lat": 18.7, "lon": -67.9, "track": [
        {"h": 0, "lat": 18.6, "lon": -67.6, "ashore": 0.0},
        {"h": 24, "lat": 18.5, "lon": -67.4, "ashore": 0.0},
        {"h": 48, "lat": 18.4, "lon": -67.2, "ashore": 0.6},
        {"h": 72, "lat": 18.4, "lon": -67.2, "ashore": 0.9}]}
    assert track_position(mass, 0) == (18.6, -67.6, False)   # now, not the detected spot
    lat, lon, ashore = track_position(mass, 12)
    assert abs(lat - 18.55) < 1e-9 and abs(lon + 67.5) < 1e-9 and not ashore
    assert track_position(mass, 48)[2] is True
    assert track_position(mass, 500)[:2] == (18.4, -67.2)    # clamped to the track
    assert track_position({"lat": 1, "lon": 2}, 24) is None
    print("  pipeline track lookup: OK")


def main() -> int:
    print("test_track_position")
    test_track_position()
    print("test_shore_exposure_and_adjustment")
    test_shore_exposure_and_adjustment()
    print("test_haversine_known_distance")
    test_haversine_known_distance()
    print("test_nearest_zone_matches_geography")
    test_nearest_zone_matches_geography()
    print("test_risk_mapping_and_fallback")
    test_risk_mapping_and_fallback()
    print("\nAll risk-overlay assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
