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

"""Offline tests for pipeline.afai on synthetic AFAI fields.

Run from repo root:
    python -m tests.test_afai

No network: the ERDDAP response is built in memory.
"""
from __future__ import annotations

import datetime as dt
import sys

import numpy as np

from pipeline import afai

LATS = np.round(np.arange(18.0, 18.6, 0.015), 6)
LONS = np.round(np.arange(-69.0, -68.4, 0.015), 6)


def _background() -> np.ndarray:
    """Clear water with a smooth haze gradient, the kind a fixed threshold trips on."""
    rng = np.random.default_rng(0)
    yy, xx = np.meshgrid(np.arange(len(LATS)), np.arange(len(LONS)), indexing="ij")
    return -0.0015 + 0.00002 * xx + rng.normal(0, 0.00005, (len(LATS), len(LONS)))


def _field_with_patch() -> np.ndarray:
    v = _background()
    v[20:23, 20:24] += 0.003       # a 12-cell sargassum patch
    v[5:15, 30:38] = np.nan        # a cloud
    v[15, 33] = v[15, 33] + 0.003  # bright cell on the cloud edge: must be ignored
    v[30, 5] = 0.004               # saturated single cell: must be ignored
    return v


def test_parse_erddap_json() -> None:
    rows = []
    for day in ("2026-11-03T12:00:00Z", "2026-11-04T12:00:00Z"):
        for la in LATS[:3]:
            for lo in LONS[:4]:
                rows.append([day, float(la), float(lo), None if lo == LONS[0] else 0.001])
    fields = afai.parse_erddap_json({"table": {
        "columnNames": ["time", "latitude", "longitude", "AFAI"], "rows": rows}})
    assert [f.date for f in fields] == [dt.date(2026, 11, 3), dt.date(2026, 11, 4)]
    assert fields[0].values.shape == (3, 4)
    assert np.isnan(fields[0].values[:, 0]).all() and (fields[0].values[:, 1:] == 0.001).all()
    print("  ERDDAP table -> one grid per day: OK")


def test_query_uses_index_arithmetic() -> None:
    q = afai._query(3, {"min_lat": 17.3, "max_lat": 21.5, "min_lon": -72.1, "max_lon": -67.3})
    assert q.startswith("AFAI[last-2:1:last][(17.3):1:(21.5)][(-72.1):1:(-67.3)]"), q
    assert afai._query(1, {"min_lat": 1, "max_lat": 2, "min_lon": 3, "max_lon": 4}).startswith(
        "AFAI[last:1:last]")
    print("  query string: OK")


def test_flag_finds_patch_not_edges() -> None:
    flagged, _, clear = afai.flag_field(_field_with_patch())
    assert flagged[20:23, 20:24].all(), "planted patch not flagged"
    assert not flagged[15, 33], "cloud-edge cell was flagged"
    assert not flagged[30, 5], "saturated cell was flagged"
    assert not clear[5:15, 30:38].any(), "cloud counted as clear"
    assert flagged.sum() == 12, f"expected only the patch, got {flagged.sum()} cells"
    print("  background removal, cloud edges and saturation: OK")


def test_clusters_and_area() -> None:
    field = afai.AfaiField(dt.date(2026, 11, 4), LATS, LONS, _field_with_patch())
    flagged, anomaly, clear, day_index = afai.combine_latest([field])
    patches = afai.clusters(flagged, anomaly, LATS, LONS, [field.date], day_index)
    assert len(patches) == 1
    p = patches[0]
    assert p["source"] == "usf-afai" and p["n_cells"] == 12 and p["observed_on"] == "2026-11-04"
    assert abs(p["centroid_lat"] - LATS[21]) < 0.01 and abs(p["centroid_lon"] - LONS[21:23].mean()) < 0.01
    # ~12 cells of ~2.6 km² at ~3.5 % cover each -> roughly 1 km² covered.
    assert 0.3 < p["area_km2"] < 3.0, p["area_km2"]
    assert p["geom_wkt"].startswith("POLYGON")
    print("  cluster centroid, covered area, polygon: OK")


def test_combine_keeps_latest_clear_view() -> None:
    old = _background()
    old[20:23, 20:24] += 0.003                 # seen 2 days ago
    new = _background()
    new[18:26, 18:27] = np.nan                 # cloud over it today
    fields = [afai.AfaiField(dt.date(2026, 11, 2), LATS, LONS, old),
              afai.AfaiField(dt.date(2026, 11, 4), LATS, LONS, new)]
    flagged, anomaly, clear, day_index = afai.combine_latest(fields)
    assert flagged[20:23, 20:24].all(), "older clear sighting lost under today's cloud"
    assert (day_index[20:23, 20:24] == 0).all() and day_index[0, 0] == 1
    print("  latest clear view per cell: OK")


def test_compare_and_gap_fill() -> None:
    field = _field_with_patch()
    _, _, clear = afai.flag_field(field)
    afai_patches = [
        {"centroid_lat": 18.32, "centroid_lon": -68.68, "area_km2": 1.0},   # S2 saw it too
        {"centroid_lat": 18.10, "centroid_lon": -68.90, "area_km2": 0.5},   # S2 missed it
    ]
    s2 = [
        {"centroid_lat": 18.33, "centroid_lon": -68.70, "area_km2": 2.0},   # confirmed
        {"centroid_lat": 18.45, "centroid_lon": -68.50, "area_km2": 2.0},   # not confirmed
        {"centroid_lat": LATS[10], "centroid_lon": LONS[34], "area_km2": 2.0},  # under cloud
    ]
    summary, unmatched = afai.compare(s2, afai_patches, clear, LATS, LONS, match_km=5)
    assert summary == {"s2_patches": 3, "s2_checkable": 2, "s2_confirmed": 1,
                       "afai_clusters": 2, "afai_matched": 1}, summary
    assert unmatched == [afai_patches[1]]
    print("  agreement counts and unmatched clusters: OK")


def main() -> int:
    tests = [
        test_parse_erddap_json,
        test_query_uses_index_arithmetic,
        test_flag_finds_patch_not_edges,
        test_clusters_and_area,
        test_combine_keeps_latest_clear_view,
        test_compare_and_gap_fill,
    ]
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print("\nAll afai assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
