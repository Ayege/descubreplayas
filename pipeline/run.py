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

"""Pipeline orchestrator: detect -> ocean -> drift -> store -> dispatch."""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time

from pipeline import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def _elapsed(start: float) -> str:
    return f"{time.perf_counter() - start:.1f}s"


def _afai_observed_at(patch: dict) -> str | None:
    """AFAI comes from MODIS/VIIRS early-afternoon passes (~17-18 UTC here)."""
    day = patch.get("observed_on")
    return f"{day}T17:30:00+00:00" if day else None


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    parser = argparse.ArgumentParser(description="Run the sargassum pipeline once.")
    parser.add_argument("--dry-run", action="store_true",
                        help="detect, drift and forecast, but write nothing and send no alerts")
    args = parser.parse_args(argv)
    dry = args.dry_run

    run_start = time.perf_counter()
    now = dt.datetime.now(dt.timezone.utc)
    logger.info("=== Pipeline run starting (UTC %s)%s ===", now.isoformat(),
                " — DRY RUN, nothing is written" if dry else "")

    # ------------------------------------------------------------------
    # Step 1: detect
    # ------------------------------------------------------------------
    patches: list[dict] = []
    t = time.perf_counter()
    try:
        from pipeline.detect import detect_sargassum  # heavy import, keep local
        end_date = dt.date.today()
        start_date = end_date - dt.timedelta(days=7)
        date_range = (start_date.isoformat(), end_date.isoformat())
        logger.info("detect: querying %s → %s", *date_range)
        gdf = detect_sargassum(date_range)
        if gdf is None or gdf.empty:
            logger.warning("detect: no patches found — pipeline will still store empty forecasts.")
        else:
            # Normalise GeoDataFrame rows into plain dicts for downstream steps.
            for _, row in gdf.iterrows():
                patches.append({
                    "centroid_lat": row.geometry.centroid.y,
                    "centroid_lon": row.geometry.centroid.x,
                    "area_km2": float(row.get("area_km2", 0.0)),
                    "covered_km2": row.get("covered_km2"),
                    "observed_at": row.get("observed_at"),
                    "geom_wkt": row.geometry.wkt,
                    "source": config.DETECTION_SOURCE,
                })
            logger.info("detect: %d patch(es) in %s", len(patches), _elapsed(t))
    except Exception:
        logger.exception("detect step failed — skipping downstream steps that need patches")
        patches = []

    # ------------------------------------------------------------------
    # Step 1b: independent check against NOAA/USF daily AFAI, and fill in
    #          masses Sentinel-2 had no clear view of (see pipeline/afai.py).
    # ------------------------------------------------------------------
    afai_summary: dict | None = None
    if config.AFAI_ENABLED:
        t = time.perf_counter()
        try:
            from pipeline.afai import check_and_fill
            gap_patches, afai_summary = check_and_fill(patches)
            for gp in gap_patches:
                gp["observed_at"] = _afai_observed_at(gp)
            patches.extend(gap_patches)
            logger.info("afai: +%d gap-fill patch(es) in %s", len(gap_patches), _elapsed(t))
        except Exception:
            logger.exception("afai step failed — continuing with Sentinel-2 detections only")

    # ------------------------------------------------------------------
    # Step 2: ocean forcing — gridded currents + wind over the whole EEZ,
    #         from the oldest observation (masses are caught up to now
    #         first) to the end of the forecast.
    # ------------------------------------------------------------------
    from pipeline.ocean import ForcingField
    horizon = config.FORECAST_HOURS_PHYSICS
    field = None
    if patches:
        t = time.perf_counter()
        oldest = now - dt.timedelta(days=config.DRIFT_MAX_AGE_DAYS)
        observed = []
        for p in patches:
            try:
                observed.append(dt.datetime.fromisoformat(str(p["observed_at"])))
            except (KeyError, TypeError, ValueError):
                pass
        start = max(min(observed + [now]), oldest) - dt.timedelta(hours=2)
        try:
            from pipeline.ocean import fetch_forcing
            field = fetch_forcing(start, now + dt.timedelta(hours=horizon + 2))
            logger.info("ocean: forcing from %s (%s) in %s", start.isoformat(),
                        field.current_kind, _elapsed(t))
        except Exception:
            logger.exception("ocean step failed — masses will not move; only those "
                             "already at the coast are counted")
    if field is None:
        field = ForcingField.uniform(now, horizon, 0.0, 0.0)

    # ------------------------------------------------------------------
    # Step 3: drift / risk — particle ensemble for every patch.
    # ------------------------------------------------------------------
    forecasts: list[dict] = []
    t = time.perf_counter()
    try:
        from pipeline.drift import project_drift
        forecasts = project_drift(patches, field, hours=[0, 24, 48, horizon], now=now)
        logger.info("drift: %d zone forecast(s) in %s", len(forecasts), _elapsed(t))
    except Exception:
        logger.exception("drift step failed — no forecasts will be stored")
        forecasts = []

    if dry:
        print("\nDRY RUN — zone forecasts (nothing stored, no alerts sent):")
        print(f"  {'zone':<14}{'risk':<8}{'p(arrive)':>10}{'eta h':>7}   probs")
        for fc in forecasts:
            print(f"  {fc['zone_name']:<14}{fc['risk_level']:<8}{fc['arrival_probability']:>10.2f}"
                  f"{fc['eta_hours'] if fc['eta_hours'] is not None else '—':>7}   {fc['risk_probs']}")
        logger.info("=== Dry run complete in %s ===", _elapsed(run_start))
        return 0

    # ------------------------------------------------------------------
    # Step 4: store
    # ------------------------------------------------------------------
    if patches:
        t = time.perf_counter()
        try:
            from pipeline.store import upsert_detections
            upsert_detections(patches)
            logger.info("store detections: done in %s", _elapsed(t))
        except Exception:
            logger.exception("store detections step failed")

    if afai_summary is not None:
        try:
            from pipeline.store import insert_detection_check
            insert_detection_check(afai_summary)
        except Exception:
            logger.exception("store detection check failed")

    if forecasts:
        t = time.perf_counter()
        try:
            from pipeline.store import upsert_forecasts, upsert_zones
            # Auto-seed any new zones that are in config but not yet in the DB.
            zone_map = upsert_zones(config.ZONES)  # {name -> id}
            # Map forecast zone_ids to actual DB ids; drop any that are still missing.
            unmapped = []
            for fc in forecasts:
                db_id = zone_map.get(fc["zone_name"])
                if db_id is not None:
                    fc["zone_id"] = db_id
                else:
                    unmapped.append(fc["zone_name"])
            if unmapped:
                logger.warning(
                    "store forecasts: skipping %d forecast(s) for zones not in DB: %s",
                    len(unmapped), unmapped,
                )
                forecasts = [fc for fc in forecasts if zone_map.get(fc["zone_name"]) is not None]
            upsert_forecasts(forecasts)
            logger.info("store forecasts: done in %s", _elapsed(t))
        except Exception:
            logger.exception("store forecasts step failed")

        # This run's per-zone model inputs, so the extended model trains on
        # exactly what it is later asked to predict from.
        try:
            from pipeline.features import RISK_TO_INT, zone_feature_rows
            from pipeline.store import upsert_zone_features, upsert_zones
            zone_map = upsert_zones(config.ZONES)
            zones_with_ids = [{**z, "id": zone_map[z["name"]]}
                              for z in config.ZONES if z["name"] in zone_map]
            upsert_zone_features(zone_feature_rows(
                dt.datetime.now(dt.timezone.utc).isoformat(),
                zones_with_ids,
                patches,
                {fc["zone_name"]: RISK_TO_INT.get(fc.get("risk_level", "none"), 0)
                 for fc in forecasts},
            ))
        except Exception:
            logger.exception("store zone features failed")

    # ------------------------------------------------------------------
    # Step 5: dispatch alerts
    # ------------------------------------------------------------------
    t = time.perf_counter()
    try:
        from pipeline.dispatch import dispatch_alerts
        dispatch_alerts(forecasts)
        logger.info("dispatch: done in %s", _elapsed(t))
    except Exception:
        logger.exception("dispatch step failed")

    # ------------------------------------------------------------------
    # Step 6: ML extended forecast (7 / 14 / 21 days)
    # ------------------------------------------------------------------
    t = time.perf_counter()
    try:
        from pipeline.model import get_forecaster
        from pipeline.store import upsert_ml_forecasts, upsert_zones

        forecaster = get_forecaster()

        # Periodically retrain: count pipeline runs via a simple counter file.
        _counter_path = config.__file__.replace("config.py", ".run_count")
        try:
            run_count = int(open(_counter_path).read().strip()) + 1
        except Exception:
            run_count = 1
        try:
            open(_counter_path, "w").write(str(run_count))
        except Exception:
            pass

        if run_count % config.ML_RETRAIN_EVERY_N_RUNS == 1 or not forecaster.is_trained:
            logger.info("ml: training model (run #%d) …", run_count)
            from supabase import create_client
            sb_client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
            if forecaster.train(sb_client) and forecaster.evaluation is not None:
                try:
                    from pipeline.store import insert_model_evaluation
                    insert_model_evaluation(forecaster.evaluation, forecaster.uses_ml)
                except Exception:
                    logger.exception("ml: could not store the model evaluation")

        # Build physics risk lookup: {zone_name -> risk int (0-3)}
        from pipeline.features import RISK_TO_INT
        physics_risks: dict[str, int] = {}
        for fc in forecasts:
            physics_risks[fc["zone_name"]] = RISK_TO_INT.get(fc.get("risk_level", "none"), 0)

        run_date = dt.datetime.now(dt.timezone.utc).date()
        ml_preds = forecaster.predict(
            zones=config.ZONES,
            detections=patches,
            current_physics_risks=physics_risks,
            run_date=run_date,
        )

        if ml_preds:
            # Map zone names to DB ids (zone_map already fetched in step 4 if
            # forecasts ran; re-fetch cheaply if not).
            zone_map: dict[str, int] = {}
            try:
                zone_map = upsert_zones(config.ZONES)
            except Exception:
                logger.warning("ml: could not fetch zone map — skipping store.")

            if zone_map:
                for p in ml_preds:
                    db_id = zone_map.get(p["zone_name"])
                    if db_id is not None:
                        p["zone_id"] = db_id
                ml_preds = [p for p in ml_preds if "zone_id" in p]
                upsert_ml_forecasts(ml_preds)

        logger.info(
            "ml: %d extended forecast(s) stored in %s", len(ml_preds), _elapsed(t)
        )
    except Exception:
        logger.exception("ml step failed — pipeline still completed")

    logger.info("=== Pipeline run complete in %s ===", _elapsed(run_start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
