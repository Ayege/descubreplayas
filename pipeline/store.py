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

"""Storage step: upsert detections and forecasts into Supabase (Postgres + PostGIS)."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from supabase import create_client, Client

from pipeline import config

logger = logging.getLogger(__name__)


def _float_or_none(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN -> None


# Columns added after first release. If sql/schema.sql has not been re-run
# yet, PostgREST rejects them; writes then retry without them instead of
# losing the whole batch.
_NEW_COLUMNS = {
    "detections": ("observed_at", "covered_km2", "track"),
    "beach_forecasts": ("exposure",),
    "forecasts": ("arrival_probability", "risk_probs", "model_version"),
    "ml_forecasts": ("probs", "model_version"),
}


def _write(table: str, rows: list[dict], upsert_on: str | None = None) -> list[dict]:
    def go(payload):
        q = _client().table(table)
        q = q.upsert(payload, on_conflict=upsert_on) if upsert_on else q.insert(payload)
        return q.execute().data or []
    try:
        return go(rows)
    except Exception as exc:
        missing = [c for c in _NEW_COLUMNS.get(table, ()) if c in str(exc)]
        if not missing:
            raise
        logger.warning("%s: column(s) %s missing — run sql/schema.sql. Writing without them.",
                       table, missing)
        return go([{k: v for k, v in r.items() if k not in missing} for r in rows])


def _client() -> Client:
    """Return a Supabase service-role client."""
    if not config.SUPABASE_URL or not config.SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be set in the environment."
        )
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


# ---------------------------------------------------------------------------
# Detections
# ---------------------------------------------------------------------------

def upsert_detections(patches: list[dict]) -> list[dict]:
    """Insert detected sargassum patches into the detections table.

    Args:
        patches: list of dicts with at minimum:
                 {centroid_lat, centroid_lon, area_km2,
                  geom_wkt (polygon WKT string), source (optional)}

    Returns:
        List of inserted rows as returned by Supabase.
    """
    if not patches:
        logger.info("upsert_detections: nothing to insert.")
        return []

    run_at = dt.datetime.now(dt.timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for p in patches:
        geom_wkt = p.get("geom_wkt") or p.get("geometry_wkt", "")
        centroid_wkt = (
            f"POINT({p['centroid_lon']} {p['centroid_lat']})"
        )
        rows.append(
            {
                "run_at": run_at,
                "geom": f"SRID=4326;{geom_wkt}",
                "centroid": f"SRID=4326;{centroid_wkt}",
                "area_km2": float(p["area_km2"]),
                "source": p.get("source", config.DETECTION_SOURCE),
                "observed_at": p.get("observed_at"),
                "covered_km2": _float_or_none(p.get("covered_km2")),
                "track": p.get("track"),
            }
        )

    try:
        inserted = _write("detections", rows)
        logger.info("upsert_detections: inserted %d row(s).", len(inserted))
        return inserted
    except Exception:
        logger.exception("upsert_detections failed.")
        raise


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------

def upsert_forecasts(forecasts: list[dict]) -> list[dict]:
    """Insert zone forecasts into the forecasts table.

    Args:
        forecasts: list of dicts from drift.project_drift():
                   {zone_id, zone_name, risk_level, eta_hours, eta_timestamp}

    Returns:
        List of inserted rows as returned by Supabase.
    """
    if not forecasts:
        logger.info("upsert_forecasts: nothing to insert.")
        return []

    run_at = dt.datetime.now(dt.timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for f in forecasts:
        rows.append(
            {
                "run_at": run_at,
                "zone_id": int(f["zone_id"]),
                "risk_level": f["risk_level"],
                "eta_hours": f.get("eta_hours"),
                "eta_timestamp": f.get("eta_timestamp"),
                "horizons": f.get("horizons"),
                "arrival_probability": f.get("arrival_probability"),
                "risk_probs": f.get("risk_probs"),
                "model_version": f.get("model_version", config.DRIFT_MODEL_VERSION),
            }
        )

    try:
        inserted = _write("forecasts", rows)
        logger.info("upsert_forecasts: inserted %d row(s).", len(inserted))
        return inserted
    except Exception:
        logger.exception("upsert_forecasts failed.")
        raise


# ---------------------------------------------------------------------------
# ML extended forecasts
# ---------------------------------------------------------------------------

def upsert_ml_forecasts(ml_forecasts: list[dict]) -> list[dict]:
    """Upsert ML extended forecasts (7/14/21-day) into the ml_forecasts table.

    Args:
        ml_forecasts: list of dicts from model.SargassumForecaster.predict():
                      {zone_id, lead_days, risk_level, confidence, method, valid_at}

    Each (run_at, zone_id, lead_days) triple is unique — the upsert replaces
    a row from an earlier run in the same wall-clock minute if one exists.
    """
    if not ml_forecasts:
        logger.info("upsert_ml_forecasts: nothing to insert.")
        return []

    run_at = dt.datetime.now(dt.timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for f in ml_forecasts:
        rows.append({
            "run_at": run_at,
            "zone_id": int(f["zone_id"]),
            "lead_days": int(f["lead_days"]),
            "risk_level": f["risk_level"],
            "confidence": float(f.get("confidence", 0.0)),
            "method": f.get("method", "seasonal"),
            "valid_at": f["valid_at"],
            "probs": f.get("probs"),
            "model_version": f.get("model_version", config.ML_MODEL_VERSION),
        })

    try:
        inserted = _write("ml_forecasts", rows, upsert_on="run_at,zone_id,lead_days")
        logger.info("upsert_ml_forecasts: upserted %d row(s).", len(inserted))
        return inserted
    except Exception:
        logger.exception("upsert_ml_forecasts failed.")
        raise


# ---------------------------------------------------------------------------
# Evaluation tables
# ---------------------------------------------------------------------------

def upsert_zone_features(rows: list[dict], model_version: str | None = None) -> int:
    """Store per-zone model inputs for one or more runs (features.zone_feature_rows).

    Each row: {run_at, zone_id, eez_area_km2, zone_area_km2, nearest_km,
    patch_count, physics_risk}, stamped with `model_version` (the drift model
    that produced physics_risk; default the current one). Upserts on
    (run_at, zone_id, model_version), so re-running a backfill is safe.
    """
    if not rows:
        return 0
    version = model_version or config.DRIFT_MODEL_VERSION
    rows = [{**r, "model_version": r.get("model_version", version)} for r in rows]
    sb = _client()
    total = 0
    for i in range(0, len(rows), 500):
        result = (
            sb.table("zone_features")
            .upsert(rows[i:i + 500], on_conflict="run_at,zone_id,model_version")
            .execute()
        )
        total += len(result.data or [])
    logger.info("upsert_zone_features: upserted %d row(s).", total)
    return total


def insert_beach_forecasts(forecasts: list[dict], zone_map: dict[str, int]) -> int:
    """Store this run's per-beach forecasts (drift.summarise_beaches)."""
    if not forecasts:
        return 0
    run_at = dt.datetime.now(dt.timezone.utc).isoformat()
    rows = [{
        "run_at": run_at,
        "beach_name": f["beach_name"],
        "zone_id": zone_map.get(f.get("zone_name")) if f.get("zone_name") else None,
        "risk_level": f["risk_level"],
        "eta_hours": f.get("eta_hours"),
        "arrival_probability": f.get("arrival_probability"),
        "risk_probs": f.get("risk_probs"),
        "horizons": f.get("horizons"),
        "model_version": f.get("model_version", config.DRIFT_MODEL_VERSION),
        "exposure": f.get("exposure"),
    } for f in forecasts]
    inserted = _write("beach_forecasts", rows)
    logger.info("insert_beach_forecasts: inserted %d row(s).", len(inserted))
    return len(inserted)


def update_zone_coasts(coasts: dict[str, str], zone_map: dict[str, int]) -> None:
    """Store each zone's owned coastline (MULTILINESTRING WKT) in zones.coast."""
    sb = _client()
    for name, wkt in coasts.items():
        zid = zone_map.get(name)
        if zid is not None and wkt:
            sb.table("zones").update({"coast": f"SRID=4326;{wkt}"}).eq("id", zid).execute()


def insert_detection_check(summary: dict) -> None:
    """Store one run's Sentinel-2 vs AFAI agreement summary."""
    keys = (
        "afai_dates", "afai_clear_frac", "s2_patches", "s2_checkable", "s2_confirmed",
        "afai_clusters", "afai_matched", "gap_filled", "gap_filled_area_km2",
    )
    row = {k: summary[k] for k in keys if k in summary}
    _client().table("detection_checks").insert(row).execute()


def insert_model_evaluation(evaluation: dict, used_for_predict: bool) -> None:
    """Store one training's walk-forward scores (see evaluate.walk_forward)."""
    _client().table("model_evaluations").insert({
        "n_rows": int(evaluation.get("n_rows", 0)),
        "n_folds": int(evaluation.get("n_folds", 0)),
        "metrics": evaluation.get("metrics", {}),
        "beats_seasonal": bool(evaluation.get("beats_seasonal", False)),
        "used_for_predict": bool(used_for_predict),
    }).execute()


# ---------------------------------------------------------------------------
# Zone cache
# ---------------------------------------------------------------------------

def fetch_zone_id_map() -> dict[str, int]:
    """Return {zone_name -> id} from the zones table."""
    try:
        sb = _client()
        result = sb.table("zones").select("id, name").execute()
        return {row["name"]: row["id"] for row in (result.data or [])}
    except Exception:
        logger.exception("fetch_zone_id_map failed.")
        raise


def upsert_zones(zones: list[dict]) -> dict[str, int]:
    """Ensure all zones from config exist in the DB; insert any that are missing.

    Args:
        zones: list of {name, center_lat, center_lon} from config.ZONES.

    Returns:
        {zone_name -> id} map (all zones, including newly created ones).
    """
    sb = _client()
    existing = fetch_zone_id_map()
    missing = [z for z in zones if z["name"] not in existing]

    if missing:
        rows = []
        for z in missing:
            d = config.ZONE_BOX_HALF_DEG
            lat, lon = z["center_lat"], z["center_lon"]
            # Build a closed polygon ring: SW -> SE -> NE -> NW -> SW
            wkt = (
                f"POLYGON(({lon-d} {lat-d}, {lon+d} {lat-d}, "
                f"{lon+d} {lat+d}, {lon-d} {lat+d}, {lon-d} {lat-d}))"
            )
            rows.append({
                "name": z["name"],
                "center_lat": lat,
                "center_lon": lon,
                "geom": f"SRID=4326;{wkt}",
            })
        try:
            result = sb.table("zones").insert(rows).execute()
            for row in (result.data or []):
                existing[row["name"]] = row["id"]
            logger.info("upsert_zones: created %d new zone(s): %s",
                        len(missing), [z["name"] for z in missing])
        except Exception:
            logger.exception("upsert_zones: failed to insert missing zones.")
            raise
    else:
        logger.debug("upsert_zones: all %d zones already in DB.", len(zones))

    return existing
