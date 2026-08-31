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

"""Standalone test for pipeline.detect: run last 7 days, print patch count, write patches.geojson.

Run from repo root:
    python -m tests.test_detect
"""

from __future__ import annotations

import datetime as dt
import logging

from pipeline import config
from pipeline.detect import detect_sargassum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main() -> int:
    today = dt.date.today()
    start = today - dt.timedelta(days=7)
    date_range = (start.isoformat(), today.isoformat())
    print(f"Detection window: {date_range[0]} .. {date_range[1]}")

    gdf = detect_sargassum(date_range, config.eez_geojson())
    print(f"Patch count: {len(gdf)}")

    if not gdf.empty:
        total_area = float(gdf["area_km2"].sum())
        print(f"Total detected area: {total_area:.2f} km^2")
        out_path = "patches.geojson"
        gdf.to_file(out_path, driver="GeoJSON")
        print(f"Wrote {out_path} (open it on https://geojson.io to eyeball).")
    else:
        print("No patches detected — try a wider window or check thresholds in config.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
