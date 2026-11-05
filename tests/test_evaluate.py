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

"""Offline tests for the extended-model training data and its evaluation.

Run from repo root:
    python -m tests.test_evaluate

No network, no Supabase: every input is synthetic.
"""
from __future__ import annotations

import datetime as dt
import sys

import numpy as np

from pipeline import evaluate
from pipeline.features import parse_point, zone_feature_rows
from pipeline.model import (
    _ConstantClassifier,
    build_training_rows,
    fit_classifier,
    predict_with_confidence,
)

T0 = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
ZONES = {
    1: {"id": 1, "name": "Punta Cana", "center_lat": 18.58, "center_lon": -68.37},
    2: {"id": 2, "name": "Puerto Plata", "center_lat": 19.80, "center_lon": -70.69},
}


def _feature_rows(days: int = 90, step_h: int = 6) -> list[dict]:
    """Two zones, one run every step_h hours. Punta Cana's risk follows a slow
    cycle driven by its zone area, so a model can learn it; Puerto Plata is
    always 'none'."""
    rows = []
    for k in range(days * 24 // step_h):
        ts = T0 + dt.timedelta(hours=k * step_h)
        cycle = (k // 40) % 4            # changes every 10 days
        rows.append({"run_at": ts.isoformat(), "zone_id": 1, "eez_area_km2": 50.0,
                     "zone_area_km2": float(cycle * 10), "nearest_km": 30.0 - cycle * 5,
                     "patch_count": 20, "physics_risk": cycle})
        rows.append({"run_at": ts.isoformat(), "zone_id": 2, "eez_area_km2": 50.0,
                     "zone_area_km2": 0.0, "nearest_km": 300.0,
                     "patch_count": 20, "physics_risk": 0})
    return rows


def test_build_training_rows_pairs_label_at_lead() -> None:
    rows = build_training_rows(_feature_rows(days=30), ZONES, leads=[7])
    assert rows, "expected training rows"
    for r in rows:
        assert r["target_ts"] - r["run_ts"] == dt.timedelta(days=7)
    # A row from day 0 at Punta Cana is labelled with the risk 7 days later.
    first = next(r for r in rows if r["zone_id"] == 1 and r["run_ts"] == T0)
    k_target = 7 * 4
    assert first["risk_int"] == (k_target // 40) % 4
    assert first["physics_now"] == 0
    # Runs in the last 7 days have no label yet and must not appear.
    last_run = T0 + dt.timedelta(days=30) - dt.timedelta(hours=6)
    assert all(r["run_ts"] <= last_run - dt.timedelta(days=7) + dt.timedelta(hours=12) for r in rows)
    print("  label pairing at T+lead: OK")


def test_walk_forward_is_purged_and_scores_baselines() -> None:
    rows = build_training_rows(_feature_rows(days=120), ZONES)
    result = evaluate.walk_forward(rows, n_folds=3, min_train=40)
    assert result["n_folds"] >= 2, result["folds"]
    for fold in result["folds"]:
        if "skipped" in fold:
            continue
        start = dt.datetime.fromisoformat(fold["test_start"])
        train = [r for r in rows if r["target_ts"] < start]
        assert len(train) == fold["n_train"]
        # Purge: no training label falls inside or after the test block.
        assert all(r["target_ts"] < start for r in train)
        # And no training row's run is inside the test block.
        assert all(r["run_ts"] < start for r in train)
    for method in ("model", "seasonal", "persistence", "majority"):
        m = result["metrics"][method]
        assert m["n"] == sum(f["n_test"] for f in result["folds"] if "skipped" not in f)
        assert set(m["by_lead"]) == {"7", "14", "21"}
        assert 0.0 <= m["macro_f1"] <= 1.0
    assert result["beats_seasonal"] == (
        result["metrics"]["model"]["macro_f1"] > result["metrics"]["seasonal"]["macro_f1"]
    )
    print(f"  walk-forward purged, {result['n_folds']} folds scored: OK")


def test_walk_forward_too_little_history() -> None:
    rows = build_training_rows(_feature_rows(days=25), ZONES)
    result = evaluate.walk_forward(rows, n_folds=3, min_train=10_000)
    assert result["n_folds"] == 0 and result["beats_seasonal"] is False
    print("  too little history -> no verdict, not a false YES: OK")


def test_prediction_maps_through_classes() -> None:
    # Training data without class 2: probability column 2 is class 3.
    X = np.array([[0.0], [0.1], [1.0], [1.1], [2.0], [2.1]] * 5)
    y = np.array([0, 0, 1, 1, 3, 3] * 5)
    clf = fit_classifier(X, y)
    labels, conf = predict_with_confidence(clf, np.array([[2.05]]))
    assert int(labels[0]) == 3, f"expected class 3 (high), got {labels[0]}"
    assert 0.0 < conf[0] <= 1.0
    # One class only -> constant classifier instead of a crash.
    single = fit_classifier(X, np.zeros(len(X), dtype=int))
    assert isinstance(single, _ConstantClassifier)
    assert int(predict_with_confidence(single, X[:2])[0][0]) == 0
    print("  predictions use clf.classes_, single class handled: OK")


def test_parse_point_formats() -> None:
    geojson = {"type": "Point", "crs": {"type": "name"}, "coordinates": [-68.37, 18.58]}
    assert parse_point(geojson) == (18.58, -68.37)
    assert parse_point("POINT(-68.37 18.58)") == (18.58, -68.37)
    assert parse_point("SRID=4326;POINT(-68.37 18.58)") == (18.58, -68.37)
    from shapely.geometry import Point
    hexwkb = Point(-68.37, 18.58).wkb_hex
    lat, lon = parse_point(hexwkb)
    assert abs(lat - 18.58) < 1e-9 and abs(lon + 68.37) < 1e-9
    assert parse_point(None) is None and parse_point({"type": "Polygon"}) is None
    print("  centroid parsing (GeoJSON, WKT, EWKB): OK")


def test_zone_feature_rows_match_prediction_inputs() -> None:
    patches = [{"centroid_lat": 18.60, "centroid_lon": -68.40, "area_km2": 3.0},
               {"centroid_lat": 21.00, "centroid_lon": -72.00, "area_km2": 1.0}]
    rows = zone_feature_rows("2026-06-01T00:00:00+00:00", list(ZONES.values()), patches,
                             {"Punta Cana": 2})
    pc = next(r for r in rows if r["zone_id"] == 1)
    assert pc["eez_area_km2"] == 4.0 and pc["patch_count"] == 2
    assert pc["zone_area_km2"] == 3.0 and pc["nearest_km"] < 5
    assert pc["physics_risk"] == 2
    assert next(r for r in rows if r["zone_id"] == 2)["physics_risk"] == 0
    print("  zone_features rows: OK")


def test_report_labels_and_scoring() -> None:
    reports = [
        {"zone_id": 1, "observed_on": "2026-07-01", "amount": "high"},
        {"zone_id": 1, "observed_on": "2026-07-01", "amount": "high"},
        {"zone_id": 1, "observed_on": "2026-07-01", "amount": "none"},  # outvoted
        {"zone_id": 2, "observed_on": "2026-07-01", "amount": "low"},
        {"zone_id": None, "observed_on": "2026-07-01", "amount": "high"},  # ignored
    ]
    labels = evaluate.report_labels(reports)
    day = dt.date(2026, 7, 1)
    assert labels == {(1, day): 3, (2, day): 1}

    physics = [
        # Issued the day before: its +24h horizon is valid on the reported day.
        {"run_at": "2026-06-30T12:00:00+00:00", "zone_id": 1, "model_version": "drift-new",
         "horizons": [{"horizon_hours": 24, "risk_level": "high",
                       "valid_at": "2026-07-01T12:00:00+00:00"}]},
        # Same day, older model: scored separately, never pooled.
        {"run_at": "2026-06-30T12:00:00+00:00", "zone_id": 1,
         "horizons": [{"horizon_hours": 24, "risk_level": "none",
                       "valid_at": "2026-07-01T12:00:00+00:00"}]},
        # Issued after the day was over: must be ignored.
        {"run_at": "2026-07-03T12:00:00+00:00", "zone_id": 1, "model_version": "drift-new",
         "horizons": [{"horizon_hours": 24, "risk_level": "none",
                       "valid_at": "2026-07-01T15:00:00+00:00"}]},
    ]
    extended = [
        {"run_at": "2026-06-24T00:00:00+00:00", "zone_id": 1, "lead_days": 7,
         "risk_level": "medium", "method": "ml", "valid_at": "2026-07-01", "model_version": "ml-new"},
        {"run_at": "2026-06-24T00:00:00+00:00", "zone_id": 2, "lead_days": 7,
         "risk_level": "low", "method": "seasonal", "valid_at": "2026-07-01", "model_version": "ml-new"},
    ]
    beach_reports = [
        {"beach_name": "Playa Bávaro", "zone_id": 1, "observed_on": "2026-07-01", "amount": "medium"},
        {"beach_name": "Playa Sosúa", "zone_id": 2, "observed_on": "2026-07-01", "amount": "none"},
    ]
    beach_labels = evaluate.report_labels(beach_reports, key="beach")
    assert beach_labels == {("Playa Bávaro", day): 2, ("Playa Sosúa", day): 0}
    beach_rows = [
        {"run_at": "2026-06-30T18:00:00+00:00", "beach_name": "Playa Bávaro", "model_version": "drift-new",
         "horizons": [{"horizon_hours": 24, "risk_level": "medium", "valid_at": "2026-07-01T18:00:00+00:00"}]},
        {"run_at": "2026-06-30T18:00:00+00:00", "beach_name": "Playa Sosúa", "model_version": "drift-new",
         "horizons": [{"horizon_hours": 24, "risk_level": "low", "valid_at": "2026-07-01T18:00:00+00:00"}]},
    ]
    out = evaluate.score_reports(labels, ZONES, physics, extended, beach_labels, beach_rows)
    m = out["metrics"]
    assert m["physics_24h @drift-new"]["n"] == 1 and m["physics_24h @drift-new"]["accuracy"] == 1.0
    assert m["physics_24h @legacy"]["accuracy"] == 0.0
    assert m["extended_7d @ml-new"]["n"] == 2 and m["extended_7d @ml-new"]["accuracy"] == 0.5
    assert m["extended_7d @ml-new"]["published_method"] == {"ml": 1, "seasonal": 1}
    assert m["beach_24h @drift-new"]["n"] == 2 and m["beach_24h @drift-new"]["accuracy"] == 0.5
    assert m["seasonal"]["n"] == 2
    # A label needs MIN reports: with 2 required, only zone 1 (3 reports) stays.
    assert evaluate.report_labels(reports, min_reports=2) == {(1, day): 3}
    assert evaluate.report_counts(reports) == {3: 1, 1: 1}
    print("  report labels (median) and forecast scoring: OK")


def test_report_training_rows() -> None:
    from pipeline.model import build_report_training_rows
    feats = _feature_rows(days=40)
    day = dt.date(2026, 7, 1)
    labels = {(1, day): 3, (2, day): 0, (9, day): 2}  # zone 9 unknown: ignored
    rows = build_report_training_rows(feats, ZONES, labels)
    assert {r["lead_days"] for r in rows} == {7, 14, 21}
    assert len(rows) == 6
    for r in rows:
        assert r["risk_int"] == labels[(r["zone_id"], day)]
        # Features come from ~lead days before the reported day.
        gap = r["target_ts"] - r["run_ts"]
        assert abs(gap - dt.timedelta(days=r["lead_days"])) <= dt.timedelta(hours=12)
    print("  report labels paired with features at D - lead: OK")


def test_calibrated_probabilities_and_brier() -> None:
    from pipeline.model import predict_proba_full
    rows = build_training_rows(_feature_rows(days=120), ZONES)
    X = np.array([r["features"] for r in rows])
    y = np.array([r["risk_int"] for r in rows])
    clf = fit_classifier(X, y)
    assert type(clf).__name__ == "CalibratedClassifierCV", type(clf).__name__
    probs = predict_proba_full(clf, X[:50])
    assert probs.shape == (50, 4) and np.allclose(probs.sum(axis=1), 1.0)
    assert evaluate.brier([0, 3], [[1, 0, 0, 0], [0, 0, 0, 1]]) == 0.0
    assert evaluate.brier([0], [[0, 0, 0, 1]]) == 2.0
    result = evaluate.walk_forward(rows, n_folds=3, min_train=40)
    for m in ("model", "seasonal", "persistence", "majority"):
        assert 0.0 <= result["metrics"][m]["brier"] <= 2.0
    print(f"  calibrated (n,4) probabilities; Brier model {result['metrics']['model']['brier']}: OK")


def main() -> int:
    tests = [
        test_report_training_rows,
        test_calibrated_probabilities_and_brier,
        test_build_training_rows_pairs_label_at_lead,
        test_walk_forward_is_purged_and_scores_baselines,
        test_walk_forward_too_little_history,
        test_prediction_maps_through_classes,
        test_parse_point_formats,
        test_zone_feature_rows_match_prediction_inputs,
        test_report_labels_and_scoring,
    ]
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print("\nAll evaluate assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
