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

"""Supabase client setup and database query helpers for the API."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from supabase import create_client, Client

from pipeline import config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Return a cached Supabase client (created once per process)."""
    if not config.SUPABASE_URL or not config.SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set.")
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def ping() -> bool:
    """Return True if the DB is reachable (used by /health)."""
    try:
        get_client().table("zones").select("id").limit(1).execute()
        return True
    except Exception:
        logger.exception("DB ping failed.")
        return False


def list_zones() -> list[dict[str, Any]]:
    result = get_client().table("zones").select("id, name, center_lat, center_lon").order("id").execute()
    return result.data or []


_FC_COLS = "id, run_at, zone_id, zone_name, risk_level, eta_hours, eta_timestamp, horizons"
_FC_NEW_COLS = ", arrival_probability, risk_probs, model_version"
_ML_COLS = "id, run_at, zone_id, zone_name, lead_days, risk_level, confidence, method, valid_at"
_ML_NEW_COLS = ", probs, model_version"


def _select(view: str, cols: str, new_cols: str, build):
    """Select with the newer columns, or without them if the view predates them.

    Keeps the API working when it is deployed before sql/schema.sql is re-run.
    """
    try:
        return build(get_client().table(view).select(cols + new_cols)).execute().data or []
    except Exception:
        logger.warning("%s: newer columns unavailable; run sql/schema.sql.", view)
        return build(get_client().table(view).select(cols)).execute().data or []


def latest_forecasts() -> list[dict[str, Any]]:
    """Return the single most-recent forecast per zone.

    Dedup happens in the forecasts_latest SQL view (DISTINCT ON per zone_id),
    not in Python — see sql/schema.sql. That avoids both the extra row
    transfer and the fragile "fetch N rows and hope every zone is still in
    the window" limit this used to rely on.
    """
    return _select("forecasts_latest", _FC_COLS, _FC_NEW_COLS, lambda q: q.order("zone_id"))


def latest_forecast_for_zone(zone_id: int) -> dict[str, Any] | None:
    rows = _select("forecasts_latest", _FC_COLS, _FC_NEW_COLS,
                   lambda q: q.eq("zone_id", zone_id).limit(1))
    return rows[0] if rows else None


def insert_subscriber(channel: str, chat_id: str, zone_id: int, role: str) -> dict[str, Any]:
    result = (
        get_client()
        .table("subscribers")
        .upsert(
            {"channel": channel, "chat_id": chat_id, "zone_id": zone_id, "role": role},
            on_conflict="channel,chat_id,zone_id",
        )
        .execute()
    )
    rows = result.data or []
    if not rows:
        logger.error(
            "insert_subscriber returned no data for chat_id=%s zone_id=%s", chat_id, zone_id
        )
        raise RuntimeError("Subscription insert returned no data.")
    return rows[0]


def list_beaches(province: str | None = None, region: str | None = None) -> list[dict[str, Any]]:
    """Return beaches, optionally filtered by province or region."""
    query = get_client().table("beaches").select(
        "id, name, province, region, latitude, longitude, access_type, access_description, "
        "entrance_fee, parking, beach_type, activities, wildlife, ecosystem, protected_area, "
        "facilities, water_conditions, best_time_to_visit, description, google_maps_url"
    )
    if province:
        query = query.eq("province", province)
    if region:
        query = query.eq("region", region)
    result = query.order("name").execute()
    return result.data or []


def list_ml_forecasts(lead_days: int | None = None) -> list[dict[str, Any]]:
    """Return the latest ML extended forecast per (zone, lead_days).

    Optionally filter to a single lead horizon (7, 14, or 21 days). Dedup
    happens in the ml_forecasts_latest SQL view — see sql/schema.sql.
    """
    def build(q):
        q = q.order("zone_id")
        return q.eq("lead_days", lead_days) if lead_days is not None else q
    return _select("ml_forecasts_latest", _ML_COLS, _ML_NEW_COLS, build)


def get_beach(name: str) -> dict[str, Any] | None:
    """Return one catalogue beach's name and position, or None if unknown."""
    result = (
        get_client()
        .table("beaches")
        .select("name, latitude, longitude")
        .eq("name", name)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def insert_report(
    *,
    beach_name: str,
    latitude: float,
    longitude: float,
    zone_id: int | None,
    amount: str,
    observed_on: str,
    source: str = "web",
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Store one visitor report of sargassum on a beach."""
    row = {
        "beach_name": beach_name,
        "latitude": latitude,
        "longitude": longitude,
        "zone_id": zone_id,
        "amount": amount,
        "observed_on": observed_on,
        "source": source,
    }
    if observed_at:
        row["observed_at"] = observed_at
    try:
        result = get_client().table("beach_reports").insert(row).execute()
    except Exception as exc:
        if "observed_at" not in str(exc):
            raise
        logger.warning("beach_reports.observed_at missing; run sql/schema.sql.")
        row.pop("observed_at", None)
        result = get_client().table("beach_reports").insert(row).execute()
    rows = result.data or []
    if not rows:
        raise RuntimeError("Report insert returned no data.")
    return rows[0]


def list_beach_forecasts() -> list[dict[str, Any]]:
    """Latest pipeline forecast per beach (beach_forecasts_latest view)."""
    result = (
        get_client()
        .table("beach_forecasts_latest")
        .select("beach_name, zone_id, risk_level, eta_hours, arrival_probability, risk_probs, "
                "horizons, run_at, model_version")
        .order("beach_name")
        .execute()
    )
    return result.data or []


def list_detections(limit: int = 2000) -> list[dict[str, Any]]:
    """Return the latest pipeline run's detected sargassum masses (lat/lon + area)."""
    result = (
        get_client()
        .table("detections_latest")
        .select("id, run_at, lat, lon, area_km2, source")
        .order("area_km2", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []
