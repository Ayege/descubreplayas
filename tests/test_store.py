"""Integration test for pipeline.store: inserts one synthetic detection + forecast, reads back.

Requires SUPABASE_URL and SUPABASE_KEY in the environment (or .env).
Run from repo root:
    python -m tests.test_store
"""
from __future__ import annotations

import sys
import logging
import datetime as dt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

SYNTHETIC_PATCH = {
    "centroid_lat": 18.58,
    "centroid_lon": -68.37,
    "area_km2": 2.5,
    "geom_wkt": (
        "POLYGON((-68.47 18.48, -68.27 18.48, -68.27 18.68, "
        "-68.47 18.68, -68.47 18.48))"
    ),
    "source": "sentinel-2-test",
}

SYNTHETIC_FORECAST = {
    "zone_id": 1,           # Punta Cana (id=1 from seed)
    "zone_name": "Punta Cana",
    "risk_level": "medium",
    "eta_hours": 18,
    "eta_timestamp": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=18)).isoformat(),
}


def main() -> int:
    from pipeline.store import upsert_detections, upsert_forecasts
    from api.db import get_client

    # --- insert ---
    det_rows = upsert_detections([SYNTHETIC_PATCH])
    assert det_rows, "upsert_detections returned no rows"
    det_id = det_rows[0]["id"]
    print(f"Inserted detection id={det_id}")

    fc_rows = upsert_forecasts([SYNTHETIC_FORECAST])
    assert fc_rows, "upsert_forecasts returned no rows"
    fc_id = fc_rows[0]["id"]
    print(f"Inserted forecast id={fc_id}")

    # --- read back ---
    sb = get_client()

    det = sb.table("detections").select("*").eq("id", det_id).single().execute()
    assert det.data, f"Could not read back detection id={det_id}"
    assert abs(det.data["area_km2"] - 2.5) < 0.001, "area_km2 mismatch"
    print(f"Read back detection: area_km2={det.data['area_km2']}")

    fc = sb.table("forecasts").select("*").eq("id", fc_id).single().execute()
    assert fc.data, f"Could not read back forecast id={fc_id}"
    assert fc.data["risk_level"] == "medium", "risk_level mismatch"
    assert fc.data["eta_hours"] == 18, "eta_hours mismatch"
    print(f"Read back forecast: risk={fc.data['risk_level']} eta={fc.data['eta_hours']}h")

    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
