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

"""Standalone LIVE test for pipeline.ocean: fetch gridded forcing and sample it off Punta Cana.

Needs CMEMS credentials and network; CI never runs it. Run from repo root:
    python -m tests.test_ocean
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np

from pipeline.ocean import fetch_forcing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# A point just offshore of Punta Cana (in open water, not on land).
PUNTA_CANA_OFFSHORE = (18.50, -68.30)


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    field = fetch_forcing(now - dt.timedelta(days=2), now + dt.timedelta(hours=72))
    assert field.u.shape == field.v.shape and field.u.ndim == 3
    assert field.has_wind, "wind should be present"
    lat, lon = PUNTA_CANA_OFFSHORE
    print(f"Currents: {field.current_kind}, {len(field.times)} h, grid {field.u.shape[1:]}")
    for h in (0, 24, 48, 72):
        t = now.timestamp() + 3600 * h
        u, v = field.current(t, lat, lon)
        wu, wv = field.wind(t, lat, lon)
        speed = float(np.hypot(wu, wv)[0])
        assert speed < 40, f"wind {speed:.1f} m/s is implausible (km/h read as m/s?)"
        print(f"  +{h:2d}h current=({u[0]:+.3f},{v[0]:+.3f}) m/s  wind=({wu[0]:+.1f},{wv[0]:+.1f}) m/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
