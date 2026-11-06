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

"""Extended sargassum zone-risk forecaster (7 / 14 / 21 days).

Architecture
------------
A gradient-boosted multi-class classifier (sklearn GradientBoostingClassifier)
trained on historical Supabase detection + zone-forecast data.

The model fills the gap between the physics drift forecast (reliable up to ~72 h)
and the monthly seasonal climatology (too coarse for planning purposes):

    0 – 72 h   → physics drift           (pipeline/drift.py, CMEMS currents)
    3 – 21 d   → THIS MODEL              (trained on historical patterns)
    > 21 d     → seasonal climatology    (dashboard/climatology.py)

Graceful degradation
--------------------
* If fewer than MIN_TRAIN_SAMPLES labelled examples exist in Supabase, training
  is skipped and predictions fall back to seasonal climatology.  A warning is
  logged so the gap is visible.
* A persisted model artefact (joblib .pkl) is loaded at inference time so the
  pipeline run is fast.  If the file is absent the fallback also applies.
* Any per-zone or per-feature error is caught and logged; the pipeline never
  crashes due to the ML step.

Training data contract
----------------------
Each training example pairs:
  X  — feature vector at pipeline run time T  (see pipeline/features.py)
  y  — the 72-h PHYSICS risk recorded for the zone at T + lead_days
       (the zone_features row closest to T+lead, within ±12 h)

Both come from the zone_features table, which the pipeline writes every run
with the same code prediction uses (pipeline/backfill_features.py fills it
from history). y is a model output, not an observation: beach reports are
the ground truth, scored in pipeline/evaluate.py.

Skill check
-----------
Every training first scores the model walk-forward (train on the past, test
on the next block of time; see pipeline/evaluate.py) next to two simple
forecasts: persistence (the zone's current drift risk holds) and seasonal.
With ML_REQUIRE_SKILL on, the best of the three by macro-F1 is published —
a method must beat the simpler ones strictly, so ties stay simple.
"""
from __future__ import annotations

import datetime as dt
import logging
import pathlib
import pickle
from typing import Any

import numpy as np

from pipeline import config
from pipeline.features import (
    INT_TO_RISK,
    build_feature_vector,
    detection_state_for_zone,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

LEAD_DAYS: list[int] = [7, 14, 21]
MIN_TRAIN_SAMPLES = 40          # below this, fall back to climatology
MODEL_PATH = pathlib.Path(
    config.__file__
).parent / ".model_cache.pkl"

# GradientBoosting hyper-parameters — shallow trees to avoid overfitting when
# training data are limited (100–500 samples expected in early deployment).
_GB_PARAMS: dict[str, Any] = {
    "n_estimators": 150,
    "max_depth": 3,
    "learning_rate": 0.08,
    "subsample": 0.8,
    "min_samples_leaf": 4,
    "random_state": 42,
}


# ── Seasonal fallback ─────────────────────────────────────────────────────────

def _seasonal_risk_for_zone(zone: dict, target_date: dt.date) -> str:
    """Derive seasonal risk from the published Caribbean monthly index.

    Imports the climatology module from the dashboard package so both the
    pipeline and the dashboard share the same seasonal model.
    """
    try:
        from dashboard.climatology import seasonal_risk, zone_region

        lat, lon = zone["center_lat"], zone["center_lon"]
        region = zone_region(lat, lon)
        return seasonal_risk(target_date.month, region)
    except Exception:
        # Last-resort: crude month-based rule
        m = target_date.month
        if m in (6, 7, 8):
            return "high"
        if m in (4, 5, 9):
            return "medium"
        if m in (3, 10):
            return "low"
        return "none"


# ── Training data assembly ────────────────────────────────────────────────────

_PAGE = 1000  # Supabase/PostgREST returns at most 1000 rows per request


def fetch_all(query_fn, page: int = _PAGE) -> list[dict]:
    """Read every row of a Supabase query, one page at a time.

    `query_fn()` must return a fresh, ORDERED query builder each call. A plain
    `.limit(5000)` silently stops at the server's 1000-row cap, which is how
    the old training code ended up seeing only the first weeks of data.
    """
    rows: list[dict] = []
    start = 0
    while True:
        chunk = query_fn().range(start, start + page - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        start += page


def parse_ts(s: str) -> dt.datetime:
    """Parse a Supabase timestamp (``+00``, ``+00:00`` or ``Z`` suffix)."""
    import re
    s = re.sub(r"\+00(:00)?$", "+00:00", str(s).replace("Z", "+00:00"))
    return dt.datetime.fromisoformat(s)


def fetch_zones_by_id(sb_client) -> dict[int, dict]:
    rows = sb_client.table("zones").select("id, name, center_lat, center_lon").execute().data or []
    return {
        int(r["id"]): {"id": int(r["id"]), "name": r["name"],
                       "center_lat": float(r["center_lat"]), "center_lon": float(r["center_lon"])}
        for r in rows
    }


def fetch_zone_features(sb_client, versions: list[str] | None = None) -> list[dict]:
    """zone_features rows, only from the drift model versions in `versions`
    (default config.ML_TRAIN_VERSIONS; pass [] for every version)."""
    versions = config.ML_TRAIN_VERSIONS if versions is None else versions

    def build():
        q = sb_client.table("zone_features").select("*")
        if versions:
            q = q.in_("model_version", versions)
        return q.order("run_at").order("zone_id")
    return fetch_all(build)


def build_training_rows(
    feature_rows: list[dict],
    zones_by_id: dict[int, dict],
    leads: list[int] | None = None,
    tolerance_h: float = 12.0,
) -> list[dict]:
    """Pair each run's zone features at T with the zone's risk at T + lead.

    Pure function (no I/O) so it can be tested offline. Returns dicts with:
      run_ts, target_ts, zone_id, zone, lead_days, features, risk_int,
      physics_now  (the zone's 72-h physics risk at T, for the persistence
                    baseline)
    The label is the physics risk recorded at T + lead (closest run within
    ±tolerance_h). It is NOT ground truth; see evaluate.score_against_reports.
    """
    import bisect

    leads = leads or LEAD_DAYS
    by_zone: dict[int, list[tuple[dt.datetime, dict]]] = {}
    for r in feature_rows:
        try:
            by_zone.setdefault(int(r["zone_id"]), []).append((parse_ts(r["run_at"]), r))
        except Exception:
            continue

    tol = dt.timedelta(hours=tolerance_h)
    out: list[dict] = []
    for zid, series in by_zone.items():
        zone = zones_by_id.get(zid)
        if zone is None:
            continue
        series.sort(key=lambda t: t[0])
        times = [t for t, _ in series]
        for ts, r in series:
            for lead in leads:
                target = ts + dt.timedelta(days=lead)
                i = bisect.bisect_left(times, target)
                best = None
                for j in (i - 1, i):
                    if 0 <= j < len(times) and abs(times[j] - target) <= tol:
                        if best is None or abs(times[j] - target) < abs(times[best] - target):
                            best = j
                if best is None:
                    continue
                fv = build_feature_vector(
                    day_of_year=ts.timetuple().tm_yday,
                    month=ts.month,
                    eez_area_km2=float(r["eez_area_km2"]),
                    zone_area_km2=float(r["zone_area_km2"]),
                    nearest_km=float(r["nearest_km"]),
                    patch_count=int(r["patch_count"]),
                    physics_risk=int(r["physics_risk"]),
                    zone_lat=zone["center_lat"],
                    zone_lon=zone["center_lon"],
                    lead_days=lead,
                )
                out.append({
                    "run_ts": ts,
                    "target_ts": target,
                    "zone_id": zid,
                    "zone": zone,
                    "lead_days": lead,
                    "features": fv,
                    "risk_int": int(series[best][1]["physics_risk"]),
                    "physics_now": int(r["physics_risk"]),
                })
    out.sort(key=lambda d: (d["run_ts"], d["zone_id"], d["lead_days"]))
    return out


# Visitor reports are a ~noon (DR) observation: 16:00 UTC.
_REPORT_HOUR_UTC = 16


def build_report_training_rows(
    feature_rows: list[dict],
    zones_by_id: dict[int, dict],
    labels: dict[tuple[int, dt.date], int],
    leads: list[int] | None = None,
    tolerance_h: float = 12.0,
) -> list[dict]:
    """Pair what visitors REPORTED on day D with the zone's features at D - lead.

    Same row shape as build_training_rows, so walk_forward and fit_classifier
    work unchanged. `labels` comes from evaluate.report_labels.
    """
    import bisect

    leads = leads or LEAD_DAYS
    by_zone: dict[int, list[tuple[dt.datetime, dict]]] = {}
    for r in feature_rows:
        try:
            by_zone.setdefault(int(r["zone_id"]), []).append((parse_ts(r["run_at"]), r))
        except Exception:
            continue
    for series in by_zone.values():
        series.sort(key=lambda t: t[0])

    tol = dt.timedelta(hours=tolerance_h)
    out: list[dict] = []
    for (zid, day), label in labels.items():
        zone = zones_by_id.get(zid)
        series = by_zone.get(zid)
        if zone is None or not series:
            continue
        times = [t for t, _ in series]
        target = dt.datetime.combine(day, dt.time(_REPORT_HOUR_UTC), dt.timezone.utc)
        for lead in leads:
            want = target - dt.timedelta(days=lead)
            i = bisect.bisect_left(times, want)
            cands = [j for j in (i - 1, i) if 0 <= j < len(times) and abs(times[j] - want) <= tol]
            if not cands:
                continue
            j = min(cands, key=lambda k: abs(times[k] - want))
            ts, r = series[j]
            out.append({
                "run_ts": ts,
                "target_ts": target,
                "zone_id": zid,
                "zone": zone,
                "lead_days": lead,
                "features": build_feature_vector(
                    day_of_year=ts.timetuple().tm_yday, month=ts.month,
                    eez_area_km2=float(r["eez_area_km2"]), zone_area_km2=float(r["zone_area_km2"]),
                    nearest_km=float(r["nearest_km"]), patch_count=int(r["patch_count"]),
                    physics_risk=int(r["physics_risk"]),
                    zone_lat=zone["center_lat"], zone_lon=zone["center_lon"], lead_days=lead,
                ),
                "risk_int": int(label),
                "physics_now": int(r["physics_risk"]),
            })
    out.sort(key=lambda d: (d["run_ts"], d["zone_id"], d["lead_days"]))
    return out


def _fetch_training_rows(sb_client) -> tuple[list[dict], str]:
    """Labelled training rows, and where their labels came from.

    Uses visitor reports as labels once they give ML_MIN_REPORT_ROWS rows:
    then the model learns what actually reaches the beaches. Until then the
    label is the physics risk at T + lead (a model output).
    """
    try:
        feature_rows = fetch_zone_features(sb_client)
        zones_by_id = fetch_zones_by_id(sb_client)
    except Exception:
        logger.exception("Failed to pull training data from Supabase.")
        return [], "none"

    try:
        from pipeline.evaluate import report_labels
        reports = fetch_all(lambda: sb_client.table("beach_reports")
                            .select("observed_on, zone_id, beach_name, amount").order("id"))
        report_rows = build_report_training_rows(feature_rows, zones_by_id, report_labels(reports))
    except Exception:
        logger.warning("Could not read beach reports; training on physics labels.", exc_info=True)
        report_rows = []

    if len(report_rows) >= config.ML_MIN_REPORT_ROWS:
        logger.info("Training data: %d rows labelled by visitor reports.", len(report_rows))
        return report_rows, "reports"
    rows = build_training_rows(feature_rows, zones_by_id)
    logger.info("Training data: %d rows labelled by physics risk (%d report rows; need %d "
                "to switch to report labels).", len(rows), len(report_rows), config.ML_MIN_REPORT_ROWS)
    return rows, "physics"


class _ConstantClassifier:
    """Stand-in when the training labels hold a single class.

    GradientBoostingClassifier refuses to fit one class, which is the normal
    state off-season (every zone 'none').
    """

    def __init__(self, label: int) -> None:
        self.classes_ = np.array([label])

    def predict_proba(self, X):
        return np.ones((len(X), 1))

    def predict(self, X):
        return np.full(len(X), self.classes_[0])


def fit_classifier(X: np.ndarray, y: np.ndarray):
    """Fit the extended-forecast classifier (shared by training and evaluation).

    Gradient boosting's raw probabilities are over-confident, so they are
    calibrated (sigmoid) on later-in-time folds: rows are in time order and
    each fold calibrates on data after the part it was fitted on. Falls back
    to the uncalibrated model if a fold lacks classes or data.
    """
    classes = np.unique(y)
    if len(classes) == 1:
        return _ConstantClassifier(int(classes[0]))
    from sklearn.ensemble import GradientBoostingClassifier
    if config.ML_CALIBRATE and len(y) >= 4 * MIN_TRAIN_SAMPLES:
        try:
            from sklearn.calibration import CalibratedClassifierCV
            from sklearn.model_selection import TimeSeriesSplit
            clf = CalibratedClassifierCV(
                GradientBoostingClassifier(**_GB_PARAMS), method="sigmoid",
                cv=TimeSeriesSplit(n_splits=3),
            )
            clf.fit(X, y)
            return clf
        except Exception:
            logger.warning("Calibration failed; using the uncalibrated model.", exc_info=True)
    clf = GradientBoostingClassifier(**_GB_PARAMS)
    clf.fit(X, y)
    return clf


def predict_proba_full(clf, X: np.ndarray) -> np.ndarray:
    """(n, 4) probabilities for none/low/medium/high, zeros for unseen classes."""
    proba = clf.predict_proba(X)
    out = np.zeros((len(X), 4))
    for col, cls in enumerate(np.asarray(clf.classes_).astype(int)):
        out[:, cls] = proba[:, col]
    return out


def predict_with_confidence(clf, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Predicted class labels and their probabilities.

    Maps through clf.classes_: when a class is missing from the training data,
    probability column i is NOT risk level i. The old code used the column
    index as the risk level, which shifted predictions whenever a class was
    absent.
    """
    proba = clf.predict_proba(X)
    idx = np.argmax(proba, axis=1)
    return np.asarray(clf.classes_)[idx].astype(int), proba[np.arange(len(idx)), idx]


# ── Model class ───────────────────────────────────────────────────────────────

class SargassumForecaster:
    """Gradient-boosted classifier for extended sargassum zone-risk prediction."""

    def __init__(self) -> None:
        self._clf = None   # sklearn classifier, None until trained or loaded
        self._trained_at: dt.datetime | None = None
        # Walk-forward test scores from the last training (evaluate.walk_forward).
        self.evaluation: dict | None = None
        # Which extended forecast is published: "ml", "persistence" or
        # "seasonal" — the best walk-forward scorer at the last training
        # (evaluate.choose_method), or "ml" when ML_REQUIRE_SKILL is off.
        self._method = "ml"

    # ── Training ──────────────────────────────────────────────────────────────

    def train(self, sb_client) -> bool:
        """Score the model on later-in-time data, then fit it on everything.

        Returns True when a model was fitted. Whether it is then USED depends
        on the walk-forward result (see `uses_ml`).
        """
        try:
            import sklearn  # noqa: F401
        except ImportError:
            logger.error(
                "scikit-learn not installed — cannot train ML model. "
                "Add scikit-learn to requirements.txt."
            )
            return False

        rows, source = _fetch_training_rows(sb_client)
        trained = self.train_on_rows(rows)
        if self.evaluation is not None:
            self.evaluation["label_source"] = source
        return trained

    def train_on_rows(self, rows: list[dict]) -> bool:
        """Train from already-assembled rows (see build_training_rows)."""
        from pipeline.evaluate import walk_forward

        if len(rows) < MIN_TRAIN_SAMPLES:
            logger.warning(
                "Only %d training examples found (need %d). "
                "Model will fall back to seasonal climatology until more data accumulate.",
                len(rows), MIN_TRAIN_SAMPLES,
            )
            return False

        self.evaluation = walk_forward(rows, n_folds=config.ML_EVAL_FOLDS)
        if not config.ML_REQUIRE_SKILL:
            self._method = "ml"
        elif self.evaluation.get("n_folds"):
            self._method = self.evaluation.get("chosen", "seasonal")
        else:
            self._method = "seasonal"   # no verdict: never publish an unscored model
        m = self.evaluation.get("metrics", {})
        logger.info(
            "ml: walk-forward over %d fold(s): macro-F1 model %s · persistence %s · seasonal %s -> publishing %s",
            self.evaluation.get("n_folds", 0),
            m.get("model", {}).get("macro_f1"), m.get("persistence", {}).get("macro_f1"),
            m.get("seasonal", {}).get("macro_f1"), self._method,
        )

        X = np.array([r["features"] for r in rows], dtype=float)
        y = np.array([r["risk_int"] for r in rows], dtype=int)
        logger.info("Fitting final model on %d examples, %d features.", len(X), X.shape[1])
        self._clf = fit_classifier(X, y)
        self._trained_at = dt.datetime.now(dt.timezone.utc)
        self._save()
        logger.info("Model training complete.  Saved to %s.", MODEL_PATH)
        return True

    def _save(self) -> None:
        try:
            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(MODEL_PATH, "wb") as f:
                pickle.dump({
                    "clf": self._clf,
                    "trained_at": self._trained_at,
                    "method": self._method,
                    "evaluation": self.evaluation,
                }, f)
        except Exception:
            logger.warning("Could not persist model to %s.", MODEL_PATH, exc_info=True)

    def load(self) -> bool:
        """Load a persisted model.  Returns True if a valid artefact was found."""
        if not MODEL_PATH.exists():
            return False
        try:
            with open(MODEL_PATH, "rb") as f:
                # Written by this pipeline: locally, or by an earlier scheduled
                # run on main and restored from that branch's Actions cache
                # (pipeline.yml), which pull requests cannot write to.
                obj = pickle.load(f)  # nosec B301
            self._clf = obj["clf"]
            self._trained_at = obj.get("trained_at")
            self.evaluation = obj.get("evaluation")
            # Older artefacts stored use_ml; ones before the skill check carry
            # no verdict and are treated as unproven (seasonal until retrained).
            self._method = obj.get("method") or ("ml" if obj.get("use_ml") else "seasonal")
            logger.info("Loaded model trained at %s.", self._trained_at)
            return True
        except Exception:
            logger.warning("Model artefact could not be loaded — will retrain.", exc_info=True)
            return False

    # ── Inference ────────────────────────────────────────────────────────────

    def predict(
        self,
        zones: list[dict],
        detections: list[dict],
        current_physics_risks: dict[str, int],
        run_date: dt.date | None = None,
    ) -> list[dict]:
        """Produce extended risk forecasts for all zones at each LEAD_DAYS.

        Args:
            zones: list of zone dicts from config.ZONES (with name, center_lat, center_lon).
            detections: current pipeline run's detected patches.
            current_physics_risks: {zone_name -> risk int (0-3)} from the 72-h drift step.
            run_date: date of the pipeline run (defaults to today UTC).

        Returns:
            list of dicts, one per (zone, lead):
            {
                zone_name, zone_id (1-based), lead_days,
                risk_level, confidence, method,
                valid_at (ISO date),
            }
        """
        if run_date is None:
            run_date = dt.datetime.now(dt.timezone.utc).date()

        results: list[dict] = []
        # None → persistence or seasonal (no model, or another method scored better).
        clf = self._clf if self._method == "ml" else None
        persist_acc = (((self.evaluation or {}).get("metrics") or {}).get("persistence") or {}).get("by_lead", {})

        for zi, zone in enumerate(zones):
            zone_id = zi + 1
            zone_name = zone["name"]
            phys = current_physics_risks.get(zone_name, 0)

            eez_area, zone_area, nearest_km, patch_count = detection_state_for_zone(
                zone, detections
            )

            for lead in LEAD_DAYS:
                target_date = run_date + dt.timedelta(days=lead)

                if clf is not None:
                    # ML prediction
                    try:
                        doy = run_date.timetuple().tm_yday
                        fv = build_feature_vector(
                            day_of_year=doy,
                            month=run_date.month,
                            eez_area_km2=eez_area,
                            zone_area_km2=zone_area,
                            nearest_km=nearest_km,
                            patch_count=patch_count,
                            physics_risk=phys,
                            zone_lat=zone["center_lat"],
                            zone_lon=zone["center_lon"],
                            lead_days=lead,
                        )
                        X = np.array([fv], dtype=float)
                        labels, confs = predict_with_confidence(clf, X)
                        risk_level = INT_TO_RISK[int(labels[0])]
                        confidence = float(confs[0])
                        full = predict_proba_full(clf, X)[0]
                        probs = {INT_TO_RISK[k]: round(float(full[k]), 3) for k in range(4)}
                        method = "ml"
                    except Exception:
                        logger.warning("ML predict failed for zone %s +%dd.",
                                       zone_name, lead, exc_info=True)
                        risk_level = _seasonal_risk_for_zone(zone, target_date)
                        confidence = 0.4
                        probs = None
                        method = "seasonal"
                elif self._method == "persistence":
                    # Today's 72-h drift risk for the zone will hold; confidence
                    # is persistence's own walk-forward accuracy at this lead.
                    risk_level = INT_TO_RISK[int(phys)]
                    confidence = float((persist_acc.get(str(lead)) or {}).get("accuracy") or 0.5)
                    probs = None
                    method = "persistence"
                else:
                    # No trained model, or seasonal scored best
                    risk_level = _seasonal_risk_for_zone(zone, target_date)
                    confidence = 0.4
                    probs = None
                    method = "seasonal"

                results.append({
                    "zone_name": zone_name,
                    "zone_id": zone_id,
                    "lead_days": lead,
                    "risk_level": risk_level,
                    "confidence": round(confidence, 3),
                    "method": method,
                    "valid_at": target_date.isoformat(),
                    "probs": probs,
                })

        return results

    @property
    def is_trained(self) -> bool:
        return self._clf is not None

    @property
    def uses_ml(self) -> bool:
        """True when predictions come from the model."""
        return self._clf is not None and self._method == "ml"

    @property
    def method(self) -> str:
        """The published extended-forecast method: ml, persistence or seasonal."""
        return self._method if (self._method != "ml" or self._clf is not None) else "seasonal"


# ── Module-level singleton (loaded on first import) ───────────────────────────

_forecaster: SargassumForecaster | None = None


def get_forecaster() -> SargassumForecaster:
    """Return the shared singleton, loading the persisted artefact if available."""
    global _forecaster
    if _forecaster is None:
        _forecaster = SargassumForecaster()
        _forecaster.load()
    return _forecaster
