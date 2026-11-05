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

"""Visitor reports of sargassum on a beach, shared by POST /reports and the
Telegram /reporte command. Reports are the ground truth pipeline/evaluate.py
scores forecasts against."""
from __future__ import annotations

import datetime as dt
import logging
import math
import unicodedata

from api import db

logger = logging.getLogger(__name__)

# The DR is UTC-4 all year (no DST), so "today" means today on the beach.
_DR_UTC_OFFSET = dt.timedelta(hours=-4)

# Spanish words people use, mapped to the forecast's four levels.
AMOUNT_WORDS = {
    "nada": "none", "ninguno": "none", "limpia": "none", "none": "none",
    "poco": "low", "poquito": "low", "low": "low",
    "bastante": "medium", "moderado": "medium", "medium": "medium",
    "mucho": "high", "muchisimo": "high", "high": "high",
}


def dr_today() -> dt.date:
    return (dt.datetime.now(dt.timezone.utc) + _DR_UTC_OFFSET).date()


def fold(text: str) -> str:
    """Lower-case, accent-free, single-spaced: "Playa Bávaro" -> "playa bavaro"."""
    norm = unicodedata.normalize("NFKD", text)
    return " ".join("".join(c for c in norm if not unicodedata.combining(c)).lower().split())


def nearest_zone_id(lat: float, lon: float, zones: list[dict]) -> int | None:
    """Zone whose centre is closest to (lat, lon), by great-circle distance."""
    best_id, best_km = None, math.inf
    for z in zones:
        p1, p2 = math.radians(lat), math.radians(float(z["center_lat"]))
        dlat = p2 - p1
        dlon = math.radians(float(z["center_lon"]) - lon)
        a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
        km = 2 * 6371.0 * math.asin(math.sqrt(a))
        if km < best_km:
            best_id, best_km = int(z["id"]), km
    return best_id


def match_beach(query: str, names: list[str]) -> tuple[str | None, list[str]]:
    """(the one beach `query` names, or None; candidates when it's ambiguous).

    Accent- and case-insensitive; "playa " is optional ("bavaro" finds
    "Playa Bávaro").
    """
    q = fold(query).removeprefix("playa ").strip()
    if not q:
        return None, []
    folded = {n: fold(n) for n in names}
    exact = [n for n, f in folded.items() if q in (f, f.removeprefix("playa "))]
    if len(exact) == 1:
        return exact[0], []
    partial = [n for n, f in folded.items() if q in f]
    if len(partial) == 1:
        return partial[0], []
    return None, sorted(exact or partial)[:8]


def store_report(beach: dict, amount: str, observed_on: dt.date, source: str) -> dict:
    """Insert one report for a catalogue beach row {name, latitude, longitude}."""
    lat, lon = float(beach["latitude"]), float(beach["longitude"])
    try:
        zone_id = nearest_zone_id(lat, lon, db.list_zones())
    except Exception:
        logger.warning("reports: could not load zones; storing report without zone.", exc_info=True)
        zone_id = None
    return db.insert_report(
        beach_name=beach["name"], latitude=lat, longitude=lon, zone_id=zone_id,
        amount=amount, observed_on=observed_on.isoformat(), source=source,
    )
