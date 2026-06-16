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
        gdf = detect_sargassum()
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
    # Step 2: ocean forcing (only fetch for up to 20 centroids to stay
    #         within free-tier CMEMS rate limits in a single pipeline run)
    # ------------------------------------------------------------------
    forcing: dict[int, dict] = {}
    if patches:
        t = time.perf_counter()
        try:
            from pipeline.ocean import get_ocean_forcing
            sample = patches[:20]
            points = [(p["centroid_lat"], p["centroid_lon"]) for p in sample]
            forcing = get_ocean_forcing(points, forecast_hours=72)
            logger.info("ocean: forcing for %d point(s) in %s", len(forcing), _elapsed(t))
        except Exception:
            logger.exception("ocean step failed — drift will use zero forcing")
            forcing = {}

    # ------------------------------------------------------------------
    # Step 3: drift / risk
    # ------------------------------------------------------------------
    forecasts: list[dict] = []
    t = time.perf_counter()
    try:
        from pipeline.drift import project_drift
        forecasts = project_drift(patches[:20], forcing)
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
            from pipeline.store import upsert_forecasts, fetch_zone_id_map
            # Ensure zone_ids match the DB (DB is source-of-truth).
            zone_map = fetch_zone_id_map()  # {name -> id}
            for fc in forecasts:
                db_id = zone_map.get(fc["zone_name"])
                if db_id is not None:
                    fc["zone_id"] = db_id
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

    logger.info("=== Pipeline run complete in %s ===", _elapsed(run_start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
