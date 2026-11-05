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

"""Offline tests for pipeline.replay: settings overrides and tuning against reports.

Run from repo root:
    python -m tests.test_replay

The database reads and the forcing download are replaced by fakes.
"""
from __future__ import annotations

import datetime as dt
import logging
import sys

from pipeline import config, replay
from pipeline.drift import run_particles
from pipeline.ocean import ForcingField

logging.basicConfig(level=logging.WARNING)

NOW = dt.datetime(2026, 7, 1, 12, tzinfo=dt.timezone.utc)
FIELD = ForcingField.uniform(NOW, 200, u=-0.5, v=0.0)
# A mass 45 km east of Punta Cana, moving west: it reaches Punta Cana ~1 day later.
PATCH = {"centroid_lat": 18.58, "centroid_lon": -67.90, "area_km2": 30.0, "covered_km2": 2.0}


def test_overrides_restore_settings() -> None:
    before = (config.WIND_DRIFT_MIN, config.WIND_DRIFT_MAX, config.RISK_AREA_MIN_KM2,
              config.RISK_AREA_FIELD)
    with replay.overrides(windage=(0.0, 0.01), threshold_scale=2.0, area_field="covered_km2"):
        assert (config.WIND_DRIFT_MIN, config.WIND_DRIFT_MAX) == (0.0, 0.01)
        assert config.RISK_AREA_MIN_KM2 == before[2] * 2
        assert config.RISK_AREA_FIELD == "covered_km2"
    after = (config.WIND_DRIFT_MIN, config.WIND_DRIFT_MAX, config.RISK_AREA_MIN_KM2,
             config.RISK_AREA_FIELD)
    assert after == before
    print("  config overrides applied and restored: OK")


def test_grid_and_chunks() -> None:
    grid = replay._grid({"a": [1, 2], "b": ["x", "y", "z"]})
    assert len(grid) == 6 and {"a": 2, "b": "z"} in grid
    runs = [(NOW + dt.timedelta(days=d), []) for d in (0, 3, 9, 11, 25)]
    groups = list(replay._chunks(runs, days=10))
    assert [len(g) for g in groups] == [3, 1, 1]
    print("  grid expansion and forcing chunks: OK")


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self


def test_tune_ranks_settings_against_reports() -> None:
    # Visitors on Punta Cana's coast the day after report a lot; Samaná none.
    reports = [{"observed_on": "2026-07-02", "zone_id": 1, "beach_name": "Playa Juanillo", "amount": "high"},
               {"observed_on": "2026-07-02", "zone_id": 3, "beach_name": "Playa Rincón", "amount": "none"}]
    zones = {i + 1: {"id": i + 1, "name": z["name"], "center_lat": z["center_lat"],
                     "center_lon": z["center_lon"]} for i, z in enumerate(config.ZONES)}

    import pipeline.model as model
    saved = (model.fetch_all, model.fetch_zones_by_id, replay.replay_particles)
    model.fetch_all = lambda build: reports
    model.fetch_zones_by_id = lambda sb: zones

    def fake_particles(runs, n_particles, particle_grid):
        for params in particle_grid:
            with replay.overrides(**params):
                yield params, NOW, run_particles([PATCH], FIELD, now=NOW, horizon_hours=72,
                                                 n_particles=n_particles)
    replay.replay_particles = fake_particles
    old_grids = (replay.PARTICLE_GRID, replay.SUMMARY_GRID)
    replay.PARTICLE_GRID = {"windage": [(0.005, 0.015)], "arrival_km": [3.0], "present_hours": [48.0]}
    replay.SUMMARY_GRID = {"area_field": ["area_km2", "covered_km2"], "threshold_scale": [1.0],
                           "beach_radius_km": [10.0]}
    try:
        results = replay.tune(None, [(NOW, [PATCH])], n_particles=8)
    finally:
        model.fetch_all, model.fetch_zones_by_id, replay.replay_particles = saved
        replay.PARTICLE_GRID, replay.SUMMARY_GRID = old_grids

    assert len(results) == 2, results
    best, worst = results
    # Outline area (30 km²) reads "high" at Punta Cana, matching the report;
    # covered area (2 km²) reads "low" and scores worse.
    assert "outline" in best["settings"] and "covered" in worst["settings"], [r["settings"] for r in results]
    assert best["macro_f1"] >= worst["macro_f1"]
    assert best["n"] >= 1
    print(f"  tuning ranks '{best['settings']}' first (macro-F1 {best['macro_f1']}): OK")


def main() -> int:
    for t in (test_overrides_restore_settings, test_grid_and_chunks,
              test_tune_ranks_settings_against_reports):
        print(f"\n{t.__name__}")
        t()
    print("\nAll replay assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
