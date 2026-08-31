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
  y  — the ACTUAL risk_level that occurred at T + lead_days
       (pulled from the forecasts table by joining on zone_id and run_at ≈ T+lead)

The join tolerance is ±12 h so jitter in the 6-h cron schedule does not lose samples.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import pathlib
import pickle
from typing import Any

import numpy as np

from pipeline import config
from pipeline.features import (
    FEATURE_NAMES,
    INT_TO_RISK,
    RISK_TO_INT,
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
        from dashboard.climatology import seasonal_risk

        # Map zone coordinates to the nearest dashboard coast region string.
        lat, lon = zone["center_lat"], zone["center_lon"]
        if lon > -69.5:
            region = "East (Punta Cana / La Romana)"
        elif lat > 19.5:
            region = "North (Puerto Plata / Cabarete)"
        elif lon < -70.5:
            region = "Southwest (Barahona / Pedernales)"
        else:
            region = "South (Santo Domingo / South Coast)"
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

def _fetch_training_rows(sb_client) -> list[dict]:
    """Pull labelled (features, risk) rows from Supabase for all past runs.

    Strategy
    --------
    For each historical pipeline run at time T:
      • Pull the detection aggregates at time T.
      • For each (zone, lead_days), find the forecast row closest to T+lead.
      • Build a feature vector + risk label.

    Returns a list of dicts:
      {zone_name, zone_lat, zone_lon, lead_days, features: list[float], risk_int: int}
    """
    # ── Pull raw tables ──────────────────────────────────────────────────────
    try:
        fc_resp = (
            sb_client.table("forecasts")
            .select("run_at, zone_id, risk_level, zones(name, center_lat, center_lon)")
            .order("run_at", desc=False)
            .limit(5000)
            .execute()
        )
        det_resp = (
            sb_client.table("detections")
            .select("run_at, area_km2, centroid")
            .order("run_at", desc=False)
            .limit(20000)
            .execute()
        )
    except Exception:
        logger.exception("Failed to pull training data from Supabase.")
        return []

    forecasts_raw: list[dict] = fc_resp.data or []
    detections_raw: list[dict] = det_resp.data or []

    if not forecasts_raw:
        return []

    # ── Group forecasts by (run_at, zone_id) ──────────────────────────────────
    import re

    def _parse_ts(s: str) -> dt.datetime:
        # Supabase returns ISO strings with +00 or Z suffix.
        s = re.sub(r"\+00(:00)?$", "+00:00", s.replace("Z", "+00:00"))
        return dt.datetime.fromisoformat(s)

    fc_by_zone: dict[int, list[tuple[dt.datetime, str]]] = {}
    for row in forecasts_raw:
        zobj = row.get("zones") or {}
        zid = row.get("zone_id")
        try:
            ts = _parse_ts(row["run_at"])
            rl = row["risk_level"]
        except Exception:
            continue
        fc_by_zone.setdefault(zid, []).append((ts, rl))

    # ── Group detections by run_at (aggregate per run) ─────────────────────
    det_by_run: dict[dt.datetime, list[dict]] = {}
    for row in detections_raw:
        try:
            ts = _parse_ts(row["run_at"])
        except Exception:
            continue
        # Bucket to nearest hour so small timestamp jitter doesn't fragment runs.
        bucket = ts.replace(minute=0, second=0, microsecond=0)
        # Decode centroid WKT "POINT(lon lat)" to lat/lon.
        centroid_wkt = row.get("centroid") or ""
        try:
            m = re.search(r"POINT\(([0-9.\-]+)\s+([0-9.\-]+)\)", centroid_wkt)
            clon, clat = (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)
        except Exception:
            clon, clat = 0.0, 0.0
        det_by_run.setdefault(bucket, []).append(
            {"lat": clat, "lon": clon, "centroid_lat": clat, "centroid_lon": clon,
             "area_km2": float(row.get("area_km2") or 0.0)}
        )

    # ── Build labelled examples ───────────────────────────────────────────────
    zones = config.ZONES
    zone_lookup: dict[int, dict] = {}
    for row in forecasts_raw:
        zid = row.get("zone_id")
        zobj = row.get("zones") or {}
        if zid and zobj and zid not in zone_lookup:
            zone_lookup[zid] = {
                "name": zobj.get("name", ""),
                "center_lat": float(zobj.get("center_lat", 0)),
                "center_lon": float(zobj.get("center_lon", 0)),
            }

    rows: list[dict] = []
    run_times = sorted(det_by_run.keys())

    for run_ts in run_times:
        dets = det_by_run[run_ts]
        run_date = run_ts.date()

        # Find the physics risk at this run for each zone (use earliest fc at/after run_ts).
        physics_at_run: dict[int, int] = {}
        for zid, fc_list in fc_by_zone.items():
            for fc_ts, rl in fc_list:
                if abs((fc_ts - run_ts).total_seconds()) <= 4 * 3600:
                    physics_at_run[zid] = RISK_TO_INT.get(rl, 0)
                    break

        for lead in LEAD_DAYS:
            target_ts = run_ts + dt.timedelta(days=lead)
            tolerance = dt.timedelta(hours=12)

            for zid, fc_list in fc_by_zone.items():
                zone = zone_lookup.get(zid)
                if zone is None:
                    continue

                # Find the actual risk closest to T + lead
                best_rl: str | None = None
                best_diff = math.inf
                for fc_ts, rl in fc_list:
                    diff = abs((fc_ts - target_ts).total_seconds())
                    if diff < best_diff and diff <= tolerance.total_seconds():
                        best_diff = diff
                        best_rl = rl

                if best_rl is None:
                    continue  # no label available for this lead

                eez_area, zone_area, nearest_km, patch_count = detection_state_for_zone(
                    zone, dets
                )
                phys = physics_at_run.get(zid, 0)
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
                rows.append({
                    "features": fv,
                    "risk_int": RISK_TO_INT.get(best_rl, 0),
                })

    logger.info("Training data assembled: %d labelled examples.", len(rows))
    return rows


# ── Model class ───────────────────────────────────────────────────────────────

class SargassumForecaster:
    """Gradient-boosted classifier for extended sargassum zone-risk prediction."""

    def __init__(self) -> None:
        self._clf = None   # sklearn classifier, None until trained or loaded
        self._trained_at: dt.datetime | None = None

    # ── Training ──────────────────────────────────────────────────────────────

    def train(self, sb_client) -> bool:
        """Train from Supabase historical data.  Returns True on success."""
        try:
            from sklearn.ensemble import GradientBoostingClassifier
            from sklearn.preprocessing import LabelEncoder
        except ImportError:
            logger.error(
                "scikit-learn not installed — cannot train ML model. "
                "Add scikit-learn to requirements.txt."
            )
            return False

        rows = _fetch_training_rows(sb_client)
        if len(rows) < MIN_TRAIN_SAMPLES:
            logger.warning(
                "Only %d training examples found (need %d). "
                "Model will fall back to seasonal climatology until more data accumulate.",
                len(rows), MIN_TRAIN_SAMPLES,
            )
            return False

        X = np.array([r["features"] for r in rows], dtype=float)
        y = np.array([r["risk_int"] for r in rows], dtype=int)

        logger.info("Training GradientBoostingClassifier on %d examples, %d features.",
                    len(X), X.shape[1])
        clf = GradientBoostingClassifier(**_GB_PARAMS)
        clf.fit(X, y)

        self._clf = clf
        self._trained_at = dt.datetime.now(dt.timezone.utc)
        self._save()
        logger.info("Model training complete.  Saved to %s.", MODEL_PATH)
        return True

    def _save(self) -> None:
        try:
            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(MODEL_PATH, "wb") as f:
                pickle.dump({"clf": self._clf, "trained_at": self._trained_at}, f)
        except Exception:
            logger.warning("Could not persist model to %s.", MODEL_PATH, exc_info=True)

    def load(self) -> bool:
        """Load a persisted model.  Returns True if a valid artefact was found."""
        if not MODEL_PATH.exists():
            return False
        try:
            with open(MODEL_PATH, "rb") as f:
                obj = pickle.load(f)
            self._clf = obj["clf"]
            self._trained_at = obj.get("trained_at")
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
        clf = self._clf  # may be None → seasonal fallback

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
                        proba = clf.predict_proba(X)[0]
                        pred_int = int(np.argmax(proba))
                        confidence = float(proba[pred_int])
                        risk_level = INT_TO_RISK[pred_int]
                        method = "ml"
                    except Exception:
                        logger.warning("ML predict failed for zone %s +%dd.",
                                       zone_name, lead, exc_info=True)
                        risk_level = _seasonal_risk_for_zone(zone, target_date)
                        confidence = 0.4
                        method = "seasonal"
                else:
                    # No trained model — use seasonal climatology
                    risk_level = _seasonal_risk_for_zone(zone, target_date)
                    confidence = 0.4
                    method = "seasonal"

                results.append({
                    "zone_name": zone_name,
                    "zone_id": zone_id,
                    "lead_days": lead,
                    "risk_level": risk_level,
                    "confidence": round(confidence, 3),
                    "method": method,
                    "valid_at": target_date.isoformat(),
                })

        return results

    @property
    def is_trained(self) -> bool:
        return self._clf is not None


# ── Module-level singleton (loaded on first import) ───────────────────────────

_forecaster: SargassumForecaster | None = None


def get_forecaster() -> SargassumForecaster:
    """Return the shared singleton, loading the persisted artefact if available."""
    global _forecaster
    if _forecaster is None:
        _forecaster = SargassumForecaster()
        _forecaster.load()
    return _forecaster
