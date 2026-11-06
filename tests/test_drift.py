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

"""Offline tests for pipeline.drift: synthetic patches in uniform currents.

Run from repo root:
    python -m tests.test_drift

Uses ocean.ForcingField.uniform, so there are no network calls. Positions
are chosen against the real coastline in pipeline/data/land.geojson.
"""
from __future__ import annotations

import datetime as dt
import logging
import sys

from pipeline import config
from pipeline.drift import project_drift, run_particles
from pipeline.ocean import ForcingField

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s: %(message)s")

NOW = dt.datetime(2026, 7, 1, 12, tzinfo=dt.timezone.utc)


def _patch(lat: float, lon: float, area: float = 30.0, **extra) -> dict:
    return {"centroid_lat": lat, "centroid_lon": lon, "area_km2": area, **extra}


def _zone(forecasts: list[dict], name: str) -> dict:
    return next(f for f in forecasts if f["zone_name"] == name)


def test_arrives_at_the_coast_ahead() -> None:
    """A mass 45 km east of Punta Cana in a 0.5 m/s westward current."""
    field = ForcingField.uniform(NOW, 96, u=-0.5, v=0.0)
    fc = project_drift([_patch(18.58, -67.90)], field, now=NOW)
    assert len(fc) == len(config.ZONES)
    pc = _zone(fc, "Punta Cana")
    assert pc["risk_level"] == "high", pc       # 30 km² arriving
    assert pc["eta_hours"] is not None and 15 <= pc["eta_hours"] <= 40, pc["eta_hours"]
    assert pc["arrival_probability"] > 0.9
    assert abs(sum(pc["risk_probs"].values()) - 1.0) < 1e-6
    # Bávaro's box overlaps Punta Cana's, but its coast is not where the mass lands.
    assert _zone(fc, "Bavaro")["risk_level"] == "none"
    assert [h["horizon_hours"] for h in pc["horizons"]] == [0, 24, 48, 72]
    assert pc["horizons"][0]["risk_level"] == "none"
    print(f"  Punta Cana arrival, ETA {pc['eta_hours']} h, p={pc['arrival_probability']}: OK")


def test_offshore_pass_is_not_an_arrival() -> None:
    """A mass 25 km off the north coast drifting west, parallel to the shore,
    used to count for every zone box it crossed."""
    field = ForcingField.uniform(NOW, 96, u=-0.6, v=0.0)
    fc = project_drift([_patch(20.05, -70.0)], field, now=NOW)
    for name in ("Puerto Plata", "Rio San Juan", "Monte Cristi"):
        assert _zone(fc, name)["risk_level"] == "none", _zone(fc, name)
    print("  offshore pass along the north coast: no arrival: OK")


def test_land_blocks_crossing() -> None:
    """Pushed north from the south coast, a mass must stop on the south coast,
    not cross Hispaniola into the northern zones."""
    field = ForcingField.uniform(NOW, 96, u=0.0, v=1.0)
    fc = project_drift([_patch(18.15, -70.35)], field, now=NOW)
    south = [_zone(fc, n)["risk_level"] for n in ("Azua", "Juan Dolio")]
    assert any(r != "none" for r in south), south
    for name in ("Puerto Plata", "Rio San Juan", "Samana"):
        assert _zone(fc, name)["risk_level"] == "none", name
    print(f"  stopped on the south coast ({south}), nothing crossed north: OK")


def test_old_observation_is_caught_up() -> None:
    """Seen 2 days ago 45 km out in a 0.5 m/s current: it reached the coast
    ~1 day ago, so it is present now (ETA 0), not still 45 km out."""
    field = ForcingField.uniform(NOW, 96, u=-0.5, v=0.0)
    seen = (NOW - dt.timedelta(days=2)).isoformat()
    pc = _zone(project_drift([_patch(18.58, -67.90, observed_at=seen)], field, now=NOW), "Punta Cana")
    assert pc["eta_hours"] == 0, pc["eta_hours"]
    assert pc["horizons"][0]["risk_level"] == "high"
    # Seen 6 days ago: it beached ~5 days ago, outside DRIFT_PRESENT_HOURS,
    # so it no longer counts as present.
    long_ago = (NOW - dt.timedelta(days=6)).isoformat()
    pc_beached = _zone(project_drift([_patch(18.58, -67.90, observed_at=long_ago)], field, now=NOW), "Punta Cana")
    assert pc_beached["risk_level"] == "none", pc_beached
    too_old = (NOW - dt.timedelta(days=config.DRIFT_MAX_AGE_DAYS + 1)).isoformat()
    pc_old = _zone(project_drift([_patch(18.58, -67.90, observed_at=too_old)], field, now=NOW), "Punta Cana")
    assert pc_old["risk_level"] == "none"
    print("  2-day-old sighting already ashore; stale sighting dropped: OK")


def test_ensemble_and_windage() -> None:
    field = ForcingField.uniform(NOW, 96, u=-0.5, v=0.0, wind_u=-8.0)
    raw = run_particles([_patch(18.58, -67.90)], field, now=NOW, n_particles=16, seed=1)
    w = raw["windage"]
    assert len(w) == 16 and w.min() >= config.WIND_DRIFT_MIN and w.max() <= config.WIND_DRIFT_MAX
    # Stronger windage pushes faster: arrival hour falls as windage rises.
    arrived = ~(raw["hour"] != raw["hour"])
    hours = raw["hour"][arrived]
    order = w[raw["member"][arrived]].argsort()
    assert hours[order][:4].mean() >= hours[order][-4:].mean()
    # Same seed -> same result.
    again = run_particles([_patch(18.58, -67.90)], field, now=NOW, n_particles=16, seed=1)
    assert (again["windage"] == w).all()
    print(f"  16 members, windage {w.min():.3f}–{w.max():.3f}, reproducible: OK")


def test_small_trace_is_low_or_none() -> None:
    field = ForcingField.uniform(NOW, 96, u=-0.5, v=0.0)
    pc = _zone(project_drift([_patch(18.58, -67.90, area=0.2)], field, now=NOW), "Punta Cana")
    assert pc["risk_level"] == "none", pc
    # A trace that arrives must not produce an ETA next to a 0 % probability.
    assert pc["arrival_probability"] == 0.0 and pc["eta_hours"] is None, pc
    print("  a 0.2 km² trace stays 'none': OK")


def test_tracks_and_exposure() -> None:
    """Tracks start where the mass is NOW (caught up from when it was seen) and
    follow the real field; exposure comes from the same field."""
    from dashboard.beaches_data import BEACHES
    from pipeline.drift import forecast_zones_and_beaches
    field = ForcingField.uniform(NOW, 96, u=+0.3, v=0.0)   # EASTWARD: away from Punta Cana
    seen = (NOW - dt.timedelta(days=2)).isoformat()
    patch = _patch(18.58, -67.90, observed_at=seen)
    zones, beaches = forecast_zones_and_beaches([patch], field, BEACHES, now=NOW, n_particles=12)
    track = patch["track"]
    assert [p["h"] for p in track] == [0, 24, 48, 72]
    # 2 days at 0.3 m/s east ≈ 52 km ≈ 0.49° of longitude before "now".
    assert track[0]["lon"] > -67.90 + 0.35, track[0]
    assert track[-1]["lon"] > track[0]["lon"], "moves east, as the field says"
    assert _zone(zones, "Punta Cana")["risk_level"] == "none"
    bavaro = next(b for b in beaches if b["beach_name"] == "Playa Bávaro")
    # East-facing beach, drift heading east: pushed away from it.
    assert bavaro["exposure"] is not None and bavaro["exposure"] < -0.9, bavaro["exposure"]
    print(f"  track caught up to lon {track[0]['lon']}, exposure Bávaro {bavaro['exposure']}: OK")


def test_isla_catalina_stops_particles() -> None:
    """Catalina (added from OpenStreetMap) is land: a mass pushed north at it
    from the south stops on its shore instead of passing through."""
    field = ForcingField.uniform(NOW, 96, u=0.0, v=0.4)
    raw = run_particles([_patch(18.30, -69.00, area=5.0)], field, now=NOW, n_particles=8)
    import numpy as np
    assert (~np.isnan(raw["hour"])).all(), "all particles should reach Catalina's shore"
    assert (raw["lat"] < 18.40).all(), "nothing passes north through the island"
    print("  particles stop at Isla Catalina: OK")


def main() -> int:
    tests = [
        test_isla_catalina_stops_particles,
        test_tracks_and_exposure,
        test_arrives_at_the_coast_ahead,
        test_offshore_pass_is_not_an_arrival,
        test_land_blocks_crossing,
        test_old_observation_is_caught_up,
        test_ensemble_and_windage,
        test_small_trace_is_low_or_none,
    ]
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print("\nAll drift assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
