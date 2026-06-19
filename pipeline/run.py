"""Pipeline orchestrator: detect -> ocean -> drift -> store -> dispatch."""
from __future__ import annotations

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


def main() -> int:  # noqa: C901
    run_start = time.perf_counter()
    logger.info("=== Pipeline run starting (UTC %s) ===",
                dt.datetime.now(dt.timezone.utc).isoformat())

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
                    "geom_wkt": row.geometry.wkt,
                    "source": config.DETECTION_SOURCE,
                })
            logger.info("detect: %d patch(es) in %s", len(patches), _elapsed(t))
    except Exception:
        logger.exception("detect step failed — skipping downstream steps that need patches")
        patches = []

    # ------------------------------------------------------------------
    # Step 2: ocean forcing (only FETCH for up to 20 centroids to stay
    #         within free-tier CMEMS rate limits in a single pipeline run).
    #         The fetched forcing is then reused for ALL patches via a
    #         nearest-sample lookup so the drift step can score every patch.
    # ------------------------------------------------------------------
    forcing: dict[int, dict] = {}
    sample_points: list[tuple[float, float]] = []
    if patches:
        t = time.perf_counter()
        try:
            from pipeline.ocean import get_ocean_forcing
            sample = patches[:20]
            sample_points = [(p["centroid_lat"], p["centroid_lon"]) for p in sample]
            forcing = get_ocean_forcing(sample_points, forecast_hours=72)
            logger.info("ocean: forcing for %d point(s) in %s", len(forcing), _elapsed(t))
        except Exception:
            logger.exception("ocean step failed — drift will use zero forcing")
            forcing = {}

    # Expand the sampled forcing to cover EVERY detected patch. Each patch
    # reuses the forcing of the nearest sampled point. This lets the drift
    # step consider all 1000s of patches (not just the 20 we fetched currents
    # for), which is essential — otherwise masses already sitting inside a
    # zone are never scored and every zone reads "none".
    full_forcing: dict[int, dict] = {}
    if patches and forcing and sample_points:
        for i, p in enumerate(patches):
            if i in forcing:
                full_forcing[i] = forcing[i]
                continue
            plat, plon = p["centroid_lat"], p["centroid_lon"]
            best_j = min(
                range(len(sample_points)),
                key=lambda j: (plat - sample_points[j][0]) ** 2
                + (plon - sample_points[j][1]) ** 2,
            )
            nearest = forcing.get(best_j)
            if nearest is not None:
                full_forcing[i] = nearest

    # ------------------------------------------------------------------
    # Step 3: drift / risk — score ALL patches, not just the sampled 20.
    # ------------------------------------------------------------------
    forecasts: list[dict] = []
    t = time.perf_counter()
    try:
        from pipeline.drift import project_drift
        forecasts = project_drift(patches, full_forcing)
        logger.info("drift: %d zone forecast(s) in %s", len(forecasts), _elapsed(t))
    except Exception:
        logger.exception("drift step failed — no forecasts will be stored")
        forecasts = []

    # ------------------------------------------------------------------
    # Step 4: store
    # ------------------------------------------------------------------
    if patches:
        t = time.perf_counter()
        try:
            from pipeline.store import upsert_detections, fetch_zone_id_map
            upsert_detections(patches)
            logger.info("store detections: done in %s", _elapsed(t))
        except Exception:
            logger.exception("store detections step failed")

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
            forecaster.train(sb_client)

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
