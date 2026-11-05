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

"""Fill the zone_features table from past runs (one-off).

Reads every stored detection and forecast, rebuilds each past run's per-zone
model inputs with features.zone_feature_rows (the same code the live pipeline
now uses), and upserts them. Safe to re-run: rows are keyed on
(run_at, zone_id).

    python -m pipeline.backfill_features            # dry run: counts only
    python -m pipeline.backfill_features --write    # write to Supabase

Reading ~170k detections takes a few minutes (1000 rows per request).
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import logging
import sys

from pipeline import config
from pipeline.features import RISK_TO_INT, parse_point, zone_feature_rows

logger = logging.getLogger(__name__)

# A run stores its detections a few minutes before its forecasts, each with
# its own now(). Pair them if they are this close.
_PAIR_WINDOW = dt.timedelta(hours=2)


def pair_runs(
    forecast_runs: dict[dt.datetime, dict[int, int]],
    detection_runs: dict[dt.datetime, list[dict]],
) -> list[tuple[dt.datetime, dict[int, int], list[dict]]]:
    """Attach each forecast run to the nearest detection run within the window.

    A forecast run with no detection run nearby gets no patches, which is
    also what the live pipeline stored when nothing was detected.
    """
    det_times = sorted(detection_runs)
    out = []
    for run_at in sorted(forecast_runs):
        i = bisect.bisect_left(det_times, run_at)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(det_times) and abs(det_times[j] - run_at) <= _PAIR_WINDOW:
                if best is None or abs(det_times[j] - run_at) < abs(best - run_at):
                    best = det_times[j]
        out.append((run_at, forecast_runs[run_at], detection_runs.get(best, []) if best else []))
    return out


def build_history_feature_rows(sb_client, zones_by_id: dict[int, dict]) -> list[dict]:
    """zone_features rows for every past forecast run (reads only)."""
    from pipeline.model import fetch_all, parse_ts

    logger.info("backfill: reading forecasts …")
    forecasts = fetch_all(lambda: sb_client.table("forecasts")
                          .select("run_at, zone_id, risk_level").order("id"))
    forecast_runs: dict[dt.datetime, dict[int, int]] = {}
    for f in forecasts:
        forecast_runs.setdefault(parse_ts(f["run_at"]), {})[int(f["zone_id"])] = \
            RISK_TO_INT.get(f["risk_level"], 0)

    logger.info("backfill: reading detections (slow) …")
    detection_runs: dict[dt.datetime, list[dict]] = {}
    skipped = 0
    for d in fetch_all(lambda: sb_client.table("detections")
                       .select("run_at, area_km2, centroid").order("id")):
        pt = parse_point(d.get("centroid"))
        if pt is None:
            skipped += 1
            continue
        detection_runs.setdefault(parse_ts(d["run_at"]), []).append(
            {"centroid_lat": pt[0], "centroid_lon": pt[1], "area_km2": float(d["area_km2"] or 0)}
        )
    logger.info("backfill: %d forecast runs, %d detection runs, %d unreadable centroids",
                len(forecast_runs), len(detection_runs), skipped)

    zones = list(zones_by_id.values())
    names = {z["id"]: z["name"] for z in zones}
    rows: list[dict] = []
    for run_at, risks, patches in pair_runs(forecast_runs, detection_runs):
        rows.extend(zone_feature_rows(
            run_at.isoformat(), zones, patches,
            {names[zid]: r for zid, r in risks.items() if zid in names},
        ))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill zone_features from history.")
    parser.add_argument("--write", action="store_true", help="upsert into Supabase")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from supabase import create_client

    from pipeline.model import fetch_zones_by_id

    sb = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    rows = build_history_feature_rows(sb, fetch_zones_by_id(sb))
    print(f"{len(rows)} zone_features rows built.")
    if args.write:
        from pipeline.store import upsert_zone_features
        upsert_zone_features(rows)
    else:
        print("Dry run: nothing written. Re-run with --write to store them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
