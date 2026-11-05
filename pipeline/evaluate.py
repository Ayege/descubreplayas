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

"""Score the forecasts honestly.

Two questions, two sections:

1. Does the 7/14/21-day model beat the seasonal fallback?  (`walk_forward`)
   Runs 6 hours apart are nearly identical, so a random train/test split
   puts near-copies of each test row in training and overstates accuracy.
   Instead the run history is cut into consecutive blocks of time: each fold
   trains only on rows whose LABEL time is before the test block starts
   (a purge, so no training label falls inside the test period) and tests on
   that block. The model is compared on the same rows with:
     seasonal     the current fallback (dashboard/climatology.py)
     persistence  "the zone's 72-h physics risk now will still hold"
     majority     "always the most common class in training"
   Labels are the physics risk at T + lead, i.e. the target the model is
   trained on. That is a model output, not an observation.

2. How do the forecasts compare with what visitors saw?  (`score_reports`)
   Beach reports (POST /reports) are the only real observations. Each
   (zone, day) gets the median reported amount, and every stored forecast
   valid that day is scored against it: physics per horizon, the extended
   forecast per lead (ML or seasonal, whichever was published) and seasonal.

Run from the repo root (reads Supabase, never writes):
    python -m pipeline.evaluate
    python -m pipeline.evaluate --from-history   # before zone_features is filled
    python -m pipeline.evaluate --json out.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import statistics
import sys
from collections import Counter

import numpy as np

from pipeline import config
from pipeline.features import INT_TO_RISK, RISK_TO_INT

logger = logging.getLogger(__name__)

_DR_OFFSET = dt.timedelta(hours=-4)  # DR is UTC-4 all year


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def brier(y_true, probs) -> float:
    """Multi-class Brier score (0 = perfect, 2 = worst) for (n, 4) probabilities.

    Deterministic forecasts count as probability 1 on their class, so
    seasonal and persistence are scored on the same scale as the model.
    """
    yt = np.asarray(y_true, dtype=int)
    onehot = np.zeros((len(yt), 4))
    onehot[np.arange(len(yt)), yt] = 1.0
    return round(float(((np.asarray(probs) - onehot) ** 2).sum(axis=1).mean()), 4)


def _onehot(pred) -> np.ndarray:
    p = np.asarray(pred, dtype=int)
    out = np.zeros((len(p), 4))
    out[np.arange(len(p)), p] = 1.0
    return out


def score(y_true, y_pred) -> dict:
    """Classification scores for 0-3 risk levels.

    macro_f1      mean F1 over the classes present: the headline number,
                  because 'none' dominates and plain accuracy rewards always
                  saying 'none'.
    mae           mean distance in levels (none→high counts 3, low→medium 1).
    pod_medium+   share of real medium/high cases the forecast caught.
    far_medium+   share of medium/high forecasts that were false alarms.
    """
    from sklearn.metrics import accuracy_score, f1_score

    yt = np.asarray(y_true, dtype=int)
    yp = np.asarray(y_pred, dtype=int)
    n = int(len(yt))
    if n == 0:
        return {"n": 0}
    real_hi, pred_hi = yt >= 2, yp >= 2
    return {
        "n": n,
        "accuracy": round(float(accuracy_score(yt, yp)), 4),
        "macro_f1": round(float(f1_score(yt, yp, average="macro", zero_division=0)), 4),
        "mae": round(float(np.abs(yt - yp).mean()), 4),
        "pod_medium+": round(float((real_hi & pred_hi).sum() / real_hi.sum()), 4) if real_hi.any() else None,
        "far_medium+": round(float((pred_hi & ~real_hi).sum() / pred_hi.sum()), 4) if pred_hi.any() else None,
        "predicted": {INT_TO_RISK[k]: int(v) for k, v in sorted(Counter(yp.tolist()).items())},
    }


def _seasonal_int(zone: dict, day: dt.date) -> int:
    from pipeline.model import _seasonal_risk_for_zone
    return RISK_TO_INT.get(_seasonal_risk_for_zone(zone, day), 0)


# ---------------------------------------------------------------------------
# 1. Walk-forward evaluation of the extended model
# ---------------------------------------------------------------------------

def walk_forward(rows: list[dict], n_folds: int = 3, min_train: int | None = None) -> dict:
    """Score the model and baselines on consecutive, later-in-time blocks.

    `rows` come from model.build_training_rows. The history is split into
    n_folds + 1 blocks of run times; fold k trains on rows whose label time is
    before block k and tests on block k. Returns
      {n_rows, n_folds, folds: [...], label_counts,
       metrics: {model|seasonal|persistence|majority: score + by_lead},
       beats_seasonal}
    beats_seasonal is True when the model's pooled macro-F1 is higher than
    seasonal's on the same test rows.
    """
    from pipeline.model import (
        MIN_TRAIN_SAMPLES,
        fit_classifier,
        predict_proba_full,
        predict_with_confidence,
    )

    min_train = MIN_TRAIN_SAMPLES if min_train is None else min_train
    result: dict = {"n_rows": len(rows), "n_folds": 0, "folds": [], "metrics": {},
                    "beats_seasonal": False}
    run_times = sorted({r["run_ts"] for r in rows})
    if len(run_times) < 2:
        return result

    n_blocks = min(n_folds + 1, len(run_times))
    blocks = [list(b) for b in np.array_split(np.array(run_times, dtype=object), n_blocks)]

    methods = ("model", "seasonal", "persistence", "majority")
    pooled: dict[str, list] = {m: [] for m in methods}
    truth: list[int] = []
    leads: list[int] = []
    model_probs: list[np.ndarray] = []

    for block in blocks[1:]:
        start, end = block[0], block[-1]
        test = [r for r in rows if start <= r["run_ts"] <= end]
        train = [r for r in rows if r["target_ts"] < start]
        fold = {"test_start": start.isoformat(), "test_end": end.isoformat(),
                "n_train": len(train), "n_test": len(test)}
        if len(train) < min_train or not test:
            fold["skipped"] = "too little training data before this block"
            result["folds"].append(fold)
            continue

        clf = fit_classifier(
            np.array([r["features"] for r in train], dtype=float),
            np.array([r["risk_int"] for r in train], dtype=int),
        )
        X_test = np.array([r["features"] for r in test], dtype=float)
        model_pred, _ = predict_with_confidence(clf, X_test)
        model_probs.append(predict_proba_full(clf, X_test))
        majority = Counter(r["risk_int"] for r in train).most_common(1)[0][0]

        pooled["model"].extend(int(p) for p in model_pred)
        pooled["seasonal"].extend(_seasonal_int(r["zone"], r["target_ts"].date()) for r in test)
        pooled["persistence"].extend(r["physics_now"] for r in test)
        pooled["majority"].extend([majority] * len(test))
        truth.extend(r["risk_int"] for r in test)
        leads.extend(r["lead_days"] for r in test)
        result["folds"].append(fold)
        result["n_folds"] += 1

    if not truth:
        return result

    lead_arr = np.array(leads)
    truth_arr = np.array(truth)
    probs_arr = np.vstack(model_probs)
    for m in methods:
        pred = np.array(pooled[m])
        entry = score(truth_arr, pred)
        entry["brier"] = brier(truth_arr, probs_arr if m == "model" else _onehot(pred))
        entry["by_lead"] = {
            str(lead): score(truth_arr[lead_arr == lead], pred[lead_arr == lead])
            for lead in sorted(set(leads))
        }
        result["metrics"][m] = entry
    result["label_counts"] = {INT_TO_RISK[k]: int(v) for k, v in sorted(Counter(truth).items())}
    result["beats_seasonal"] = bool(
        result["metrics"]["model"]["macro_f1"] > result["metrics"]["seasonal"]["macro_f1"]
    )
    return result


# ---------------------------------------------------------------------------
# 2. Forecasts vs visitor reports
# ---------------------------------------------------------------------------

def _dr_date(ts: dt.datetime) -> dt.date:
    return (ts + _DR_OFFSET).date()


def report_labels(reports: list[dict]) -> dict[tuple[int, dt.date], int]:
    """(zone_id, DR date) -> median reported amount (0-3), lower median on ties.

    A median, not a max, so one exaggerated report can't flip a zone-day.
    """
    groups: dict[tuple[int, dt.date], list[int]] = {}
    for r in reports:
        if r.get("zone_id") is None or r.get("amount") not in RISK_TO_INT:
            continue
        day = dt.date.fromisoformat(str(r["observed_on"])[:10])
        groups.setdefault((int(r["zone_id"]), day), []).append(RISK_TO_INT[r["amount"]])
    return {k: int(statistics.median_low(v)) for k, v in groups.items()}


def score_reports(
    labels: dict[tuple[int, dt.date], int],
    zones_by_id: dict[int, dict],
    physics_rows: list[dict],
    extended_rows: list[dict],
) -> dict:
    """Score stored forecasts against report labels (pure; no I/O).

    physics_rows : forecasts rows {run_at, zone_id, horizons:[{horizon_hours,
                   risk_level, valid_at}]}. For each zone-day the LATEST run
                   issued before that day ended is used, per horizon.
    extended_rows: ml_forecasts rows {run_at, zone_id, lead_days, risk_level,
                   method, valid_at}; latest run per (zone, day, lead).
    """
    from pipeline.model import parse_ts

    phys: dict[tuple[int, dt.date, int], tuple[dt.datetime, int]] = {}
    for row in physics_rows:
        run_at = parse_ts(row["run_at"])
        for h in row.get("horizons") or []:
            try:
                valid = parse_ts(h["valid_at"])
                key = (int(row["zone_id"]), _dr_date(valid), int(h["horizon_hours"]))
                risk = RISK_TO_INT.get(h["risk_level"], 0)
            except Exception:
                continue
            day_end = dt.datetime.combine(key[1] + dt.timedelta(days=1), dt.time(), dt.timezone.utc) - _DR_OFFSET
            if run_at > day_end:
                continue  # issued after the day was over: not a forecast
            if key not in phys or run_at > phys[key][0]:
                phys[key] = (run_at, risk)

    ext: dict[tuple[int, dt.date, int], tuple[dt.datetime, int, str]] = {}
    for row in extended_rows:
        try:
            key = (int(row["zone_id"]), dt.date.fromisoformat(str(row["valid_at"])[:10]),
                   int(row["lead_days"]))
            run_at = parse_ts(row["run_at"])
        except Exception:
            continue
        if key not in ext or run_at > ext[key][0]:
            ext[key] = (run_at, RISK_TO_INT.get(row["risk_level"], 0), row.get("method", ""))

    pairs: dict[str, tuple[list[int], list[int]]] = {}
    methods_used: dict[str, Counter] = {}

    def add(name: str, truth: int, pred: int) -> None:
        t, p = pairs.setdefault(name, ([], []))
        t.append(truth)
        p.append(pred)

    for (zid, day), truth in sorted(labels.items()):
        zone = zones_by_id.get(zid)
        if zone is not None:
            add("seasonal", truth, _seasonal_int(zone, day))
        for h in sorted({k[2] for k in phys}):
            if (zid, day, h) in phys:
                add(f"physics_{h}h", truth, phys[(zid, day, h)][1])
        for lead in sorted({k[2] for k in ext}):
            if (zid, day, lead) in ext:
                _, pred, method = ext[(zid, day, lead)]
                add(f"extended_{lead}d", truth, pred)
                methods_used.setdefault(f"extended_{lead}d", Counter())[method] += 1

    out = {
        "n_reports_zone_days": len(labels),
        "label_counts": {INT_TO_RISK[k]: int(v) for k, v in sorted(Counter(labels.values()).items())},
        "metrics": {name: score(t, p) for name, (t, p) in pairs.items()},
    }
    for name, counts in methods_used.items():
        out["metrics"][name]["published_method"] = dict(counts)
    return out


def score_against_reports(sb_client) -> dict:
    """Fetch reports and the forecasts valid on reported days, then score."""
    from pipeline.model import fetch_all, fetch_zones_by_id

    reports = fetch_all(lambda: sb_client.table("beach_reports")
                        .select("observed_on, zone_id, amount").order("id"))
    labels = report_labels(reports)
    if not labels:
        return {"n_reports_zone_days": 0, "metrics": {}}
    first = min(day for _, day in labels)
    since = (first - dt.timedelta(days=4)).isoformat()
    physics = fetch_all(lambda: sb_client.table("forecasts")
                        .select("run_at, zone_id, horizons").gte("run_at", since).order("id"))
    extended = fetch_all(lambda: sb_client.table("ml_forecasts")
                         .select("run_at, zone_id, lead_days, risk_level, method, valid_at")
                         .gte("valid_at", first.isoformat()).order("id"))
    return score_reports(labels, fetch_zones_by_id(sb_client), physics, extended)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_table(title: str, metrics: dict, order: list[str] | None = None) -> None:
    print(f"\n{title}")
    if not metrics:
        print("  (nothing to score yet)")
        return
    print(f"  {'method':<16}{'n':>7}{'macro-F1':>10}{'accuracy':>10}{'MAE':>7}"
          f"{'POD med+':>10}{'FAR med+':>10}{'Brier':>8}")
    for name in order or sorted(metrics):
        m = metrics.get(name)
        if not m or not m.get("n"):
            continue

        def f(v):
            return "—" if v is None else f"{v:.3f}"
        print(f"  {name:<16}{m['n']:>7}{f(m['macro_f1']):>10}{f(m['accuracy']):>10}"
              f"{f(m['mae']):>7}{f(m['pod_medium+']):>10}{f(m['far_medium+']):>10}"
              f"{f(m.get('brier')):>8}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--folds", type=int, default=config.ML_EVAL_FOLDS)
    parser.add_argument("--from-history", action="store_true",
                        help="rebuild zone features in memory from detections + forecasts "
                             "(slow; use before zone_features has been backfilled)")
    parser.add_argument("--no-reports", action="store_true")
    parser.add_argument("--json", help="also write the full results to this file")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from supabase import create_client

    from pipeline.model import build_training_rows, fetch_zone_features, fetch_zones_by_id

    sb = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    zones = fetch_zones_by_id(sb)
    if args.from_history:
        from pipeline.backfill_features import build_history_feature_rows
        feature_rows = build_history_feature_rows(sb, zones)
    else:
        feature_rows = fetch_zone_features(sb)
    rows = build_training_rows(feature_rows, zones)

    out = {"walk_forward": walk_forward(rows, n_folds=args.folds)}
    wf = out["walk_forward"]
    if rows:
        print(f"\nTraining rows: {len(rows)} from {len(feature_rows)} zone-runs, "
              f"{rows[0]['run_ts']:%Y-%m-%d} → {rows[-1]['run_ts']:%Y-%m-%d}")
    for fold in wf["folds"]:
        note = f"  SKIPPED: {fold['skipped']}" if "skipped" in fold else ""
        print(f"  fold test {fold['test_start'][:10]} → {fold['test_end'][:10]}: "
              f"train {fold['n_train']}, test {fold['n_test']}{note}")
    print(f"  test labels: {wf.get('label_counts', {})}")
    _print_table("7/14/21-day model vs baselines (walk-forward, labels = physics risk at T+lead)",
                 wf["metrics"], ["model", "seasonal", "persistence", "majority"])
    for lead in ("7", "14", "21"):
        by = {m: v["by_lead"].get(lead) for m, v in wf["metrics"].items() if v.get("by_lead")}
        _print_table(f"  lead {lead} d", {k: v for k, v in by.items() if v},
                     ["model", "seasonal", "persistence", "majority"])
    if wf["n_folds"]:
        print(f"\nBeats seasonal fallback: {'YES' if wf['beats_seasonal'] else 'NO'} "
              f"(macro-F1 {wf['metrics']['model']['macro_f1']:.3f} vs "
              f"{wf['metrics']['seasonal']['macro_f1']:.3f})")
    else:
        print("\nNot enough history to score the model yet.")

    if not args.no_reports:
        try:
            from pipeline.model import MIN_TRAIN_SAMPLES, build_report_training_rows, fetch_all
            reports = fetch_all(lambda: sb.table("beach_reports")
                                .select("observed_on, zone_id, amount").order("id"))
            rrows = build_report_training_rows(feature_rows, zones, report_labels(reports))
            if len(rrows) >= MIN_TRAIN_SAMPLES:
                out["walk_forward_reports"] = wf_r = walk_forward(rrows, n_folds=args.folds)
                _print_table("7/14/21-day model vs baselines, labels = VISITOR REPORTS",
                             wf_r["metrics"], ["model", "seasonal", "persistence", "majority"])
            else:
                print(f"\nReport-labelled rows: {len(rrows)} (need {MIN_TRAIN_SAMPLES} "
                      "to score the model against reports)")
        except Exception as exc:
            print(f"\nReport-labelled walk-forward skipped ({exc.__class__.__name__})")
        try:
            out["reports"] = score_against_reports(sb)
            print(f"\nVisitor reports: {out['reports']['n_reports_zone_days']} zone-days, "
                  f"labels {out['reports'].get('label_counts', {})}")
            _print_table("Forecasts vs visitor reports", out["reports"]["metrics"])
        except Exception as exc:  # table missing before sql/schema.sql is applied
            print(f"\nVisitor reports: could not score ({exc.__class__.__name__}: {exc})")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, default=str)
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
