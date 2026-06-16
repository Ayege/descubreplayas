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
    risk, zone, dist = risk_for_beach(bavaro, ZONES, {1: "high"})
    assert risk in {"high", "none"}, f"Unexpected risk: {risk}"
    if zone["id"] == 1:
        assert risk == "high", "Bávaro nearest Punta Cana should read high"
    assert risk in RISK_COLORS, "Risk must be a known colour key"

    # Missing forecast for the nearest zone falls back to 'none'.
    risk_none, _z, _d = risk_for_beach(bavaro, ZONES, {})
    assert risk_none == "none", f"Expected fallback 'none', got {risk_none}"
    print("  risk mapping + fallback: OK")


def main() -> int:
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
