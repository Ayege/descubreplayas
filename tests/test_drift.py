"""Offline unit test for pipeline.drift: uses synthetic patches and forcing.

Run from repo root:
    python -m tests.test_drift

The test places one 5 km² patch ~1 degree west of Punta Cana (18.58, -68.37)
and gives it a steady 3 m/s eastward current with no wind.  At 3 m/s across
~1 degree of longitude (~105 km at this latitude), the patch should arrive in
roughly 10 hours.  We assert:
  - Punta Cana is assigned a non-'none' risk level.
  - ETA is plausible (between 1 and 72 hours).
  - All 5 zones appear in the result.
"""
from __future__ import annotations

import datetime as dt
import logging
import sys

from pipeline.drift import project_drift
from pipeline import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

# Punta Cana center: 18.58, -68.37
# Place patch 1 degree to the west (lon=-69.37) at the same latitude.
_PATCH_LAT = 18.58
_PATCH_LON = -69.37   # ~1 deg west of Punta Cana
_PATCH_AREA_KM2 = 5.0

SYNTHETIC_PATCHES = [
    {
        "centroid_lat": _PATCH_LAT,
        "centroid_lon": _PATCH_LON,
        "area_km2": _PATCH_AREA_KM2,
    }
]

# 72 hourly steps of steady 3 m/s eastward current, no wind.
_STEPS = 72
_U_CURRENT = 3.0   # m/s eastward
_V_CURRENT = 0.0   # m/s northward

_BASE_TIME = dt.datetime.now(dt.timezone.utc)
_TIMES = [str(_BASE_TIME + dt.timedelta(hours=h)) for h in range(_STEPS)]

SYNTHETIC_FORCING: dict[int, dict] = {
    0: {
        "lat": _PATCH_LAT,
        "lon": _PATCH_LON,
        "times": _TIMES,
        "u_current": [_U_CURRENT] * _STEPS,
        "v_current": [_V_CURRENT] * _STEPS,
        "u_wind": [0.0] * _STEPS,
        "v_wind": [0.0] * _STEPS,
        "units": {"current": "m/s", "wind": "m/s"},
    }
}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_drift() -> None:
    forecasts = project_drift(SYNTHETIC_PATCHES, SYNTHETIC_FORCING)

    zone_names = [f["zone_name"] for f in forecasts]
    print("\nZone forecasts:")
    for f in forecasts:
        print(
            f"  {f['zone_name']:15s}  risk={f['risk_level']:6s}  "
            f"eta={f['eta_hours']}h  ts={f['eta_timestamp']}"
        )

    # All 5 config zones must appear.
    assert len(forecasts) == len(config.ZONES), (
        f"Expected {len(config.ZONES)} forecasts, got {len(forecasts)}"
    )

    # Find Punta Cana forecast.
    pc = next(f for f in forecasts if f["zone_name"] == "Punta Cana")
    assert pc["risk_level"] != "none", (
        f"Punta Cana should be hit by the synthetic patch, got risk={pc['risk_level']!r}"
    )
    assert pc["eta_hours"] is not None, "Punta Cana ETA should not be None"
    assert 1 <= pc["eta_hours"] <= 72, (
        f"ETA out of expected range: {pc['eta_hours']} h"
    )
    assert pc["eta_timestamp"] is not None, "ETA timestamp should be set"

    # Risk level must be one of the four valid values everywhere.
    valid_risks = {"none", "low", "medium", "high"}
    for f in forecasts:
        assert f["risk_level"] in valid_risks, (
            f"Invalid risk_level {f['risk_level']!r} for {f['zone_name']}"
        )

    print("\nAll assertions passed.")


def main() -> int:
    test_drift()
    return 0


if __name__ == "__main__":
    sys.exit(main())
