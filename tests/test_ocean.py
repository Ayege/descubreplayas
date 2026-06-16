"""Standalone test for pipeline.ocean: fetch forcing for one point off Punta Cana, print vectors with units.

Run from repo root:
    python -m tests.test_ocean
"""

from __future__ import annotations

import logging

from pipeline.ocean import get_ocean_forcing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# A point just offshore of Punta Cana (in open water, not on land).
PUNTA_CANA_OFFSHORE = (18.50, -68.30)


def main() -> int:
    forcing = get_ocean_forcing([PUNTA_CANA_OFFSHORE], forecast_hours=72)
    if not forcing:
        print("No forcing returned.")
        return 1

    entry = forcing[0]
    units = entry["units"]
    assert entry["u_wind"] is not None, "u_wind should be present"
    assert entry["v_wind"] is not None, "v_wind should be present"
    assert len(entry["u_wind"]) == len(entry["times"]), "u_wind length must match timestamps"
    assert len(entry["v_wind"]) == len(entry["times"]), "v_wind length must match timestamps"
    assert any(value is not None for value in entry["u_wind"]), "u_wind should contain real values"
    assert any(value is not None for value in entry["v_wind"]), "v_wind should contain real values"

    print(f"Point: lat={entry['lat']}, lon={entry['lon']}")
    print(f"Time steps: {len(entry['times'])}")
    print(f"Current units: {units['current']} | Wind units: {units['wind']}\n")

    n = min(5, len(entry["times"]))
    print("First few time steps:")
    for i in range(n):
        t = entry["times"][i]
        uc = entry["u_current"][i]
        vc = entry["v_current"][i]
        uw = entry["u_wind"][i] if entry["u_wind"] else None
        vw = entry["v_wind"][i] if entry["v_wind"] else None
        wind = f"wind=({uw:.2f},{vw:.2f}) {units['wind']}" if uw is not None else "wind=N/A"
        print(f"  {t}: current=({uc:.3f},{vc:.3f}) {units['current']} | {wind}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
