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

"""Replay past runs with the current drift model: tune it, or rebuild history.

Past runs are rebuilt from the stored detections and Copernicus' archived
currents (kept back to Nov 2020) and Open-Meteo's past wind.

    # Score settings against visitor reports (reads only):
    python -m pipeline.replay --days 60

    # Regenerate zone_features for history with the CURRENT drift model, so
    # the 7/14/21-day model can train on it (writes to Supabase):
    python -m pipeline.replay --days 150 --write-features

Tuning searches a small grid. Settings that change the particle paths
(windage range, arrival distance, catch-up window) re-run the particles;
settings that only change how arrivals are summarised (area field, threshold
scale, beach radius) re-score the same particles, so the grid stays cheap.
The ranking uses the beach-level score for the day-ahead forecast (+24 h)
when there are beach reports, the zone-level one otherwise.

Caveats: detections before November 2026 came from the old detection step
(no Cloud Score+, no background removal, no coast strip) and have no
observation time, so they are treated as seen at their run time.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import itertools
import json
import logging
import sys

import numpy as np

from pipeline import config

logger = logging.getLogger(__name__)

# Particle settings (re-run the particles).
PARTICLE_GRID = {
    "windage": [(0.005, 0.015), (0.0, 0.01), (0.01, 0.02)],
    "arrival_km": [3.0, 6.0],
    "present_hours": [48.0],
}
# Summary settings (re-score the same particles).
SUMMARY_GRID = {
    "area_field": ["area_km2", "covered_km2"],
    "threshold_scale": [0.5, 1.0, 2.0],
    "beach_radius_km": [10.0],
}
_HOURS = [0, 24, 48, 72]


@contextlib.contextmanager
def overrides(**params):
    """Temporarily set drift settings on the config module."""
    mapping = {
        "windage": ("WIND_DRIFT_MIN", "WIND_DRIFT_MAX"),
        "arrival_km": ("ARRIVAL_KM",),
        "present_hours": ("DRIFT_PRESENT_HOURS",),
        "area_field": ("RISK_AREA_FIELD",),
        "beach_radius_km": ("BEACH_RADIUS_KM",),
    }
    saved = {}
    try:
        for key, value in params.items():
            if key == "threshold_scale":
                for name in ("RISK_AREA_MIN_KM2", "RISK_AREA_LOW_MAX_KM2", "RISK_AREA_MEDIUM_MAX_KM2"):
                    saved.setdefault(name, getattr(config, name))
                    setattr(config, name, saved[name] * value)
                continue
            names = mapping[key]
            values = value if isinstance(value, tuple) else (value,)
            for name, v in zip(names, values):
                saved.setdefault(name, getattr(config, name))
                setattr(config, name, v)
        yield
    finally:
        for name, v in saved.items():
            setattr(config, name, v)


def _label(params: dict) -> str:
    parts = []
    for k, v in params.items():
        if k == "windage":
            parts.append(f"wind {v[0]*100:g}-{v[1]*100:g}%")
        elif k == "arrival_km":
            parts.append(f"arr {v:g}km")
        elif k == "present_hours":
            parts.append(f"present {v:g}h")
        elif k == "area_field":
            parts.append("covered" if v == "covered_km2" else "outline")
        elif k == "threshold_scale":
            parts.append(f"thr x{v:g}")
        elif k == "beach_radius_km":
            parts.append(f"beach {v:g}km")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Rebuilding past runs
# ---------------------------------------------------------------------------

def load_runs(sb_client, since: dt.datetime, every_hours: float) -> list[tuple[dt.datetime, list[dict]]]:
    """Past runs' patches from the detections table, one run per `every_hours`."""
    from shapely.geometry import shape

    from pipeline.features import parse_point
    from pipeline.model import fetch_all, parse_ts

    def build(cols):
        return lambda: (sb_client.table("detections").select(cols)
                        .gte("run_at", since.isoformat()).order("id"))
    try:
        rows = fetch_all(build("run_at, area_km2, covered_km2, observed_at, centroid, geom, source"))
    except Exception:  # columns missing before sql/schema.sql
        rows = fetch_all(build("run_at, area_km2, centroid, geom, source"))

    runs: dict[dt.datetime, list[dict]] = {}
    for r in rows:
        pt = parse_point(r.get("centroid"))
        if pt is None:
            continue
        run_at = parse_ts(r["run_at"])
        try:
            wkt = shape(r["geom"]).wkt if isinstance(r.get("geom"), dict) else None
        except Exception:
            wkt = None
        runs.setdefault(run_at, []).append({
            "centroid_lat": pt[0], "centroid_lon": pt[1],
            "area_km2": float(r.get("area_km2") or 0.0),
            "covered_km2": r.get("covered_km2"),
            "observed_at": r.get("observed_at") or run_at.isoformat(),
            "geom_wkt": wkt,
            "source": r.get("source"),
        })

    picked: list[tuple[dt.datetime, list[dict]]] = []
    last = None
    for run_at in sorted(runs):
        if last is None or (run_at - last).total_seconds() >= every_hours * 3600 - 1:
            picked.append((run_at, runs[run_at]))
            last = run_at
    logger.info("replay: %d stored runs since %s, %d picked (every %g h)",
                len(runs), since.date(), len(picked), every_hours)
    return picked


def _chunks(runs, days: float = 10.0):
    """Group runs so each group shares one forcing download."""
    group: list = []
    for item in runs:
        if group and (item[0] - group[0][0]).total_seconds() > days * 86400:
            yield group
            group = []
        group.append(item)
    if group:
        yield group


def replay_particles(runs, n_particles: int, particle_grid: list[dict]):
    """Yield (particle params, run_at, raw particle output) for every run."""
    from pipeline.drift import run_particles
    from pipeline.ocean import fetch_forcing

    for group in _chunks(runs):
        start = group[0][0] - dt.timedelta(days=config.DRIFT_MAX_AGE_DAYS)
        end = group[-1][0] + dt.timedelta(hours=max(_HOURS) + 2)
        field = fetch_forcing(start, end)
        for run_at, patches in group:
            for params in particle_grid:
                with overrides(**params):
                    raw = run_particles(patches, field, now=run_at, horizon_hours=max(_HOURS),
                                        n_particles=n_particles)
                yield params, run_at, raw


def _summaries(raw: dict, run_at: dt.datetime, beaches: list[dict]):
    from pipeline.drift import _risk_area, summarise, summarise_beaches

    raw = {**raw, "area": np.array([_risk_area(p) for p in raw["kept"]])}
    return summarise(raw, run_at, _HOURS), summarise_beaches(raw, beaches, run_at, _HOURS)


# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

def _grid(spec: dict) -> list[dict]:
    keys = list(spec)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(spec[k] for k in keys))]


def tune(sb_client, runs, n_particles: int) -> list[dict]:
    """Score every grid combination against visitor reports."""
    from dashboard.beaches_data import BEACHES
    from pipeline.evaluate import report_labels, score_reports
    from pipeline.model import fetch_all, fetch_zones_by_id

    reports = fetch_all(lambda: sb_client.table("beach_reports")
                        .select("observed_on, zone_id, beach_name, amount").order("id"))
    zone_labels = report_labels(reports)
    beach_labels = report_labels(reports, key="beach")
    if not zone_labels and not beach_labels:
        print("No visitor reports yet: nothing to tune against.")
        return []
    zones_by_id = fetch_zones_by_id(sb_client)
    db_id = {z["name"]: zid for zid, z in zones_by_id.items()}

    rows: dict[str, tuple[list, list]] = {}
    for pparams, run_at, raw in replay_particles(runs, n_particles, _grid(PARTICLE_GRID)):
        for sparams in _grid(SUMMARY_GRID):
            params = {**pparams, **sparams}
            label = _label(params)
            with overrides(**sparams):
                zones_fc, beach_fc = _summaries(raw, run_at, BEACHES)
            zr, br = rows.setdefault(label, ([], []))
            zr.extend({"run_at": run_at.isoformat(), "zone_id": db_id.get(f["zone_name"]),
                       "horizons": f["horizons"], "model_version": label}
                      for f in zones_fc if db_id.get(f["zone_name"]) is not None)
            br.extend({"run_at": run_at.isoformat(), "beach_name": f["beach_name"],
                       "horizons": f["horizons"], "model_version": label} for f in beach_fc)

    results = []
    for label, (zr, br) in rows.items():
        out = score_reports(zone_labels, zones_by_id, zr, [], beach_labels, br)
        m = out["metrics"]
        primary = m.get(f"beach_24h @{label}") or m.get(f"physics_24h @{label}") or {}
        results.append({
            "settings": label,
            "primary": "beach_24h" if f"beach_24h @{label}" in m else "physics_24h",
            "n": primary.get("n", 0),
            "macro_f1": primary.get("macro_f1"),
            "mae": primary.get("mae"),
            "pod_medium+": primary.get("pod_medium+"),
            "far_medium+": primary.get("far_medium+"),
            "metrics": m,
        })
    results.sort(key=lambda r: (r["macro_f1"] is None, -(r["macro_f1"] or 0), r["mae"] or 9))
    return results


# ---------------------------------------------------------------------------
# Regenerating zone_features with the current model
# ---------------------------------------------------------------------------

def rebuild_features(sb_client, runs, n_particles: int, write: bool) -> int:
    """zone_features for past runs, physics_risk from the CURRENT drift model."""
    from pipeline.features import RISK_TO_INT, zone_feature_rows
    from pipeline.model import fetch_zones_by_id

    if write:  # zones added to config since the last live run
        from pipeline.store import upsert_zones
        upsert_zones(config.ZONES)
    zones_by_id = fetch_zones_by_id(sb_client)
    by_name = {z["name"]: z for z in zones_by_id.values()}
    zones_with_ids = [by_name[z["name"]] for z in config.ZONES if z["name"] in by_name]
    total = 0
    batch: list[dict] = []
    for _, run_at, raw in replay_particles(runs, n_particles, [{}]):
        from pipeline.drift import summarise
        zones_fc = summarise(raw, run_at, _HOURS)
        risks = {f["zone_name"]: RISK_TO_INT.get(f["risk_level"], 0) for f in zones_fc}
        batch.extend(zone_feature_rows(run_at.isoformat(), zones_with_ids, raw["kept"], risks))
        if write and len(batch) >= 500:
            from pipeline.store import upsert_zone_features
            total += upsert_zone_features(batch, model_version=config.DRIFT_MODEL_VERSION)
            batch = []
    if write and batch:
        from pipeline.store import upsert_zone_features
        total += upsert_zone_features(batch, model_version=config.DRIFT_MODEL_VERSION)
    elif not write:
        total = len(batch)
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=float, default=60, help="how far back to replay")
    parser.add_argument("--every", type=float, default=None,
                        help="hours between replayed runs (default 24 for tuning, 6 for features)")
    parser.add_argument("--particles", type=int, default=12,
                        help="particles per patch (fewer than live, for speed)")
    parser.add_argument("--write-features", action="store_true",
                        help="regenerate zone_features with the current drift model (writes)")
    parser.add_argument("--dry-features", action="store_true",
                        help="like --write-features but only count the rows")
    parser.add_argument("--json", help="write the full results to this file")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "copernicusmarine", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from supabase import create_client
    sb = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)

    if args.write_features or args.dry_features:
        runs = load_runs(sb, since, args.every or 6)
        n = rebuild_features(sb, runs, args.particles, write=args.write_features)
        verb = "upserted" if args.write_features else "built (not written)"
        print(f"{n} zone_features rows {verb} for {len(runs)} runs, "
              f"model_version={config.DRIFT_MODEL_VERSION}.")
        return 0

    runs = load_runs(sb, since, args.every or 24)
    results = tune(sb, runs, args.particles)
    if results:
        print(f"\n{'settings':<62}{'scored on':>11}{'n':>6}{'macro-F1':>10}{'MAE':>7}"
              f"{'POD med+':>10}{'FAR med+':>10}")
        for r in results[:20]:
            def f(v):
                return "—" if v is None else f"{v:.3f}"
            print(f"{r['settings']:<62}{r['primary']:>11}{r['n']:>6}{f(r['macro_f1']):>10}"
                  f"{f(r['mae']):>7}{f(r['pod_medium+']):>10}{f(r['far_medium+']):>10}")
        print("\nCurrent settings: "
              f"wind {config.WIND_DRIFT_MIN*100:g}-{config.WIND_DRIFT_MAX*100:g}%, "
              f"arrival {config.ARRIVAL_KM:g} km, area {config.RISK_AREA_FIELD}. "
              "Change them with the env vars in the README once a winner is clear on enough reports.")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
