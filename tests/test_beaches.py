"""Offline dataset-integrity test for the DR beach explorer.

Run from repo root:
    python -m tests.test_beaches
"""
from __future__ import annotations

import sys

from dashboard.beaches_data import (
    BEACH_FIELDS,
    BEACHES,
    REGION_COLORS,
    all_activities,
    all_provinces,
    all_regions,
    beaches_with_maps,
)

# DR bounding box (generous) for coordinate sanity checks.
_LAT_MIN, _LAT_MAX = 17.3, 20.2
_LON_MIN, _LON_MAX = -72.2, -68.0

_REQUIRED_NON_EMPTY = (
    "name", "province", "region", "access_type", "access_description",
    "entrance_fee", "beach_type", "activities", "best_time_to_visit", "description",
)


def main() -> int:
    assert len(BEACHES) >= 25, f"Expected at least 25 beaches, got {len(BEACHES)}"

    names_seen: set[str] = set()
    for b in BEACHES:
        # Every documented field is present.
        for field in BEACH_FIELDS:
            assert field in b, f"Beach {b.get('name', '?')} missing field '{field}'"

        # Required textual/list fields are non-empty.
        for field in _REQUIRED_NON_EMPTY:
            assert b[field], f"Beach {b['name']} has empty required field '{field}'"

        # Unique names.
        assert b["name"] not in names_seen, f"Duplicate beach name: {b['name']}"
        names_seen.add(b["name"])

        # Coordinates within the DR bounding box.
        assert _LAT_MIN <= b["latitude"] <= _LAT_MAX, f"{b['name']} lat out of range: {b['latitude']}"
        assert _LON_MIN <= b["longitude"] <= _LON_MAX, f"{b['name']} lon out of range: {b['longitude']}"

        # Region must have a colour mapping.
        assert b["region"] in REGION_COLORS, f"{b['name']} region missing colour: {b['region']}"

        # Booleans are real booleans.
        assert isinstance(b["parking"], bool), f"{b['name']} parking not bool"
        assert isinstance(b["protected_area"], bool), f"{b['name']} protected_area not bool"

    # Derived helpers work.
    enriched = beaches_with_maps()
    assert all("google_maps_url" in b for b in enriched), "google_maps_url not added"
    assert enriched[0]["google_maps_url"].startswith("https://"), "bad maps url"

    assert len(all_provinces()) >= 6, "Expected beaches across at least 6 provinces"
    assert len(all_activities()) >= 10, "Expected a rich activity taxonomy"
    assert len(all_regions()) == len(REGION_COLORS), "Region helper / colour mismatch"

    print(f"OK: {len(BEACHES)} beaches, {len(all_provinces())} provinces, "
          f"{len(all_regions())} regions, {len(all_activities())} activities.")
    print("All beach dataset assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
