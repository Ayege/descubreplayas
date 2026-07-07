"""Seed the Supabase `beaches` table from the local dataset (idempotent upsert).

Prerequisites:
  - Run sql/schema.sql in the Supabase SQL editor first (creates the beaches table).
  - SUPABASE_URL and SUPABASE_KEY (service-role) set in the environment / .env.

Run from repo root:
    python -m scripts.seed_beaches
"""
from __future__ import annotations

import logging
import sys

from dashboard.beaches_data import beaches_with_maps
from pipeline import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _row(beach: dict) -> dict:
    """Map a dataset beach dict to a beaches-table row, including PostGIS geom."""
    return {
        "name": beach["name"],
        "province": beach["province"],
        "region": beach["region"],
        "latitude": beach["latitude"],
        "longitude": beach["longitude"],
        "geom": f"SRID=4326;POINT({beach['longitude']} {beach['latitude']})",
        "access_type": beach.get("access_type"),
        "access_description": beach.get("access_description"),
        "entrance_fee": beach.get("entrance_fee"),
        "parking": bool(beach.get("parking", True)),
        "beach_type": beach.get("beach_type", []),
        "activities": beach.get("activities", []),
        "wildlife": beach.get("wildlife", []),
        "ecosystem": beach.get("ecosystem"),
        "protected_area": bool(beach.get("protected_area", False)),
        "facilities": beach.get("facilities", []),
        "water_conditions": beach.get("water_conditions"),
        "best_time_to_visit": beach.get("best_time_to_visit"),
        "description": beach.get("description"),
        "google_maps_url": beach.get("google_maps_url"),
    }


def main() -> int:
    if not config.SUPABASE_URL or not config.SUPABASE_KEY:
        logger.error("SUPABASE_URL and SUPABASE_KEY must be set in the environment.")
        return 1

    from supabase import create_client

    sb = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    rows = [_row(b) for b in beaches_with_maps()]

    logger.info("Upserting %d beaches into Supabase...", len(rows))
    sb.table("beaches").upsert(rows, on_conflict="name", returning="minimal").execute()
    logger.info("Done. %d beaches upserted.", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
