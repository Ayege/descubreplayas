# Copyright 2026 Ayesha Yege
#
# This file is DATA, not code. It is licensed under the Creative Commons
# Attribution 4.0 International License (CC BY 4.0) -- NOT the Apache License
# that covers the rest of this repository.
#
#     https://creativecommons.org/licenses/by/4.0/
#
# Share and adapt it freely, including commercially, provided you give
# appropriate credit. See LICENSE-DATA.md for the attribution line to use.

"""Per-spot physical calibration for the Dominican Republic surf ratings.

WHY THIS EXISTS
---------------
A wave forecast on its own says nothing about whether a beach is *good for
surfing*. 1.5 m at 11 s is a great day at Playa Encuentro and a closed-out
mess at Playa Cosón; an 18 kt ENE trade wind is perfect at Kite Beach and
ruins Playa Grande the same afternoon. Turning a forecast into a rating needs
per-spot knowledge, which is what this file holds.

Every spot here already appears in `beaches_data.BEACHES` — `beach` is the
join key and must match that dataset's `name` exactly (see
`tests/test_surf.py`, which enforces it).

WHAT IS *NOT* HERE
------------------
No descriptive prose: no break-type blurbs, no "best season", no spot guides.
Everything the app shows about surf is fetched live from the wave model (see
dashboard/surf.py). This file holds only the physical constants needed to
interpret that forecast for a given beach -- which way it faces, and what
swell it works on. If it cannot change hour to hour, the UI does not show it.

FIELD NOTES
-----------
shore_normal_deg
    Compass bearing of the outward shore normal: the direction a swell must
    travel *from* to hit the beach square-on. A north-facing beach is ~0deg,
    an east-facing beach ~90deg. Estimated from the coastline's orientation at
    each beach's coordinates, so treat it as +/- 15deg; refining these is the
    single highest-value improvement to the ratings.
swell_window_deg
    Total angular width of usable swell exposure, centred on the shore normal.
    Wide (140deg+) for an open beach facing the Atlantic; narrow for a bay or
    a headland-shadowed break.
min_rideable_m / ideal_m / max_manageable_m
    Offshore *swell* height (not breaking face height) that this spot needs to
    start working, works best at, and stops being rideable above. A shallow
    reef needs less swell than a deep-water beach break to produce the same
    wave. `min_rideable_m` is 0 at every wind-sport spot: a kite lagoon is at
    its best with no swell at all, so only `max_manageable_m` -- the point
    where the swell becomes a hazard -- carries meaning there.
face_factor
    Rough multiplier from offshore swell height to breaking face height, used
    only for the "surf height" display. Shoaling reefs amplify; deep beach
    breaks do not.
ideal_wind_kt
    Wind-sport spots only: the wind window kiters and windsurfers want.
"""
from __future__ import annotations

# Sport keys. A spot is rated by whichever sport it is known for; the two
# scoring paths in dashboard/surf.py are genuinely different (see that module).
SPORT_SURF = "surf"
SPORT_WIND = "wind"   # kitesurf / windsurf / wing foil


SURF_SPOTS: list[dict] = [
    # ------------------------------------------------- North coast (Atlantic)
    {
        "beach": "Playa Encuentro",
        "sport": SPORT_SURF,
        "shore_normal_deg": 20,
        "swell_window_deg": 150,
        "min_rideable_m": 0.5,
        "ideal_m": (1.0, 2.4),
        "max_manageable_m": 4.0,
        "face_factor": 1.25,
    },
    {
        "beach": "Playa Grande",
        "sport": SPORT_SURF,
        "shore_normal_deg": 15,
        "swell_window_deg": 140,
        "min_rideable_m": 0.7,
        "ideal_m": (1.2, 2.2),
        "max_manageable_m": 3.2,
        "face_factor": 1.15,
    },
    {
        "beach": "Playa El Bretón",
        "sport": SPORT_SURF,
        "shore_normal_deg": 5,
        "swell_window_deg": 130,
        "min_rideable_m": 0.7,
        "ideal_m": (1.0, 2.0),
        "max_manageable_m": 3.0,
        "face_factor": 1.12,
    },
    {
        "beach": "Cabarete Beach",
        "sport": SPORT_WIND,
        "shore_normal_deg": 25,
        "swell_window_deg": 110,
        "min_rideable_m": 0.0,
        "ideal_m": (0.4, 1.2),
        "max_manageable_m": 2.5,
        "face_factor": 1.0,
        "ideal_wind_kt": (14, 25),
    },
    {
        "beach": "Kite Beach",
        "sport": SPORT_WIND,
        "shore_normal_deg": 15,
        "swell_window_deg": 120,
        "min_rideable_m": 0.0,
        "ideal_m": (0.4, 1.2),
        "max_manageable_m": 2.5,
        "face_factor": 1.0,
        "ideal_wind_kt": (15, 26),
    },
    # ---------------------------------------------------- Samaná Peninsula
    {
        "beach": "Playa Bonita",
        "sport": SPORT_SURF,
        "shore_normal_deg": 355,
        "swell_window_deg": 130,
        "min_rideable_m": 0.5,
        "ideal_m": (0.7, 1.6),
        "max_manageable_m": 2.6,
        "face_factor": 1.12,
    },
    {
        "beach": "Playa Cosón",
        "sport": SPORT_SURF,
        "shore_normal_deg": 340,
        "swell_window_deg": 120,
        "min_rideable_m": 0.5,
        "ideal_m": (0.6, 1.3),
        "max_manageable_m": 2.2,
        "face_factor": 1.08,
    },
    {
        "beach": "Playa El Valle",
        "sport": SPORT_SURF,
        "shore_normal_deg": 350,
        "swell_window_deg": 110,
        "min_rideable_m": 0.7,
        "ideal_m": (0.9, 1.8),
        "max_manageable_m": 2.8,
        "face_factor": 1.12,
    },
    {
        "beach": "Playa Punta Popy",
        "sport": SPORT_WIND,
        "shore_normal_deg": 5,
        "swell_window_deg": 110,
        "min_rideable_m": 0.0,
        "ideal_m": (0.3, 1.0),
        "max_manageable_m": 2.0,
        "face_factor": 0.95,
        "ideal_wind_kt": (13, 22),
    },
    # ------------------------------------------------------------ East coast
    {
        "beach": "Playa Macao",
        "sport": SPORT_SURF,
        "shore_normal_deg": 70,
        "swell_window_deg": 140,
        "min_rideable_m": 0.5,
        "ideal_m": (0.8, 1.7),
        "max_manageable_m": 2.8,
        "face_factor": 1.15,
    },
    {
        "beach": "Playa Uvero Alto",
        "sport": SPORT_SURF,
        "shore_normal_deg": 65,
        "swell_window_deg": 140,
        "min_rideable_m": 0.5,
        "ideal_m": (0.7, 1.5),
        "max_manageable_m": 2.5,
        "face_factor": 1.12,
    },
    {
        "beach": "Playa Cabeza de Toro",
        "sport": SPORT_WIND,
        "shore_normal_deg": 85,
        "swell_window_deg": 100,
        "min_rideable_m": 0.0,
        "ideal_m": (0.2, 0.8),
        "max_manageable_m": 1.8,
        "face_factor": 0.9,
        "ideal_wind_kt": (12, 22),
    },
    {
        "beach": "Playa Blanca",
        "sport": SPORT_WIND,
        "shore_normal_deg": 100,
        "swell_window_deg": 90,
        "min_rideable_m": 0.0,
        "ideal_m": (0.2, 0.7),
        "max_manageable_m": 1.5,
        "face_factor": 0.9,
        "ideal_wind_kt": (12, 20),
    },
]


# Fast lookup: beach name -> spot record.
SPOTS_BY_BEACH: dict[str, dict] = {s["beach"]: s for s in SURF_SPOTS}


# ---------------------------------------------------------------------------
# Region-derived profiles, so EVERY beach gets a live rating
# ---------------------------------------------------------------------------
# The 13 records above are the spots worth naming; the other 43 beaches in the
# dataset still deserve an answer to "is there surf today?" — usually "no", and
# a visitor benefits from being told that rather than shown a blank.
#
# Rating a beach needs to know which way it faces, and there is no coastline
# geometry in this repo to derive that per beach. What IS solid is the
# orientation of each stretch of DR coast, which is the same fact
# dashboard/climatology.py already leans on for sargassum exposure. So an
# uncurated beach inherits its region's coastal orientation, and every reading
# built from one is marked `precision: "region"` — the UI says so, because a
# region-level normal can be 30-40 deg out for an individual cove.
REGION_SHORE_NORMAL: dict[str, int] = {
    "North (Puerto Plata / Cabarete)": 10,     # north shore, faces N
    "East (Punta Cana / La Romana)": 85,       # east shore, faces E
    "Samaná Peninsula": 355,                   # north-facing peninsula shore
    "South (Santo Domingo / South Coast)": 185,   # Caribbean side, faces S
    "Southwest (Barahona / Pedernales)": 215,     # faces SW into the Bay
}
_DEFAULT_SHORE_NORMAL = 90

# Words in a beach's own `water_conditions` that mark it as swell-sheltered.
# Read off beaches_data rather than judged beach by beach, so this stays
# honest: it is the dataset's own description doing the classifying.
_SHELTERED_WORDS = ("calm", "sheltered", "protected", "lagoon", "tidal pool",
                    "reef-protected", "reef-guarded", "bay water")
_EXPOSED_WORDS = ("surf", "strong", "current", "open atlantic", "open water",
                  "chop", "wave", "rip")

# Two generic profiles. A sheltered beach needs a big, well-aimed swell before
# anything breaks at all; an exposed one works on ordinary swell.
_PROFILE_SHELTERED = {
    "swell_window_deg": 90,
    "min_rideable_m": 0.9,
    "ideal_m": (1.2, 2.0),
    "max_manageable_m": 2.8,
    "face_factor": 0.85,
}
_PROFILE_EXPOSED = {
    "swell_window_deg": 130,
    "min_rideable_m": 0.6,
    "ideal_m": (0.9, 1.9),
    "max_manageable_m": 3.0,
    "face_factor": 1.10,
}

# Activities that mean the beach is ridden with a kite or a sail, not a board.
_WIND_ACTIVITIES = {"Kitesurfing", "Windsurfing", "Foiling"}


def _is_sheltered(beach: dict) -> bool:
    """Classify a beach as swell-sheltered from its own water_conditions text."""
    text = (beach.get("water_conditions") or "").lower()
    if any(w in text for w in _EXPOSED_WORDS):
        return False
    return any(w in text for w in _SHELTERED_WORDS)


def derived_profile(beach: dict) -> dict:
    """Build a region-derived rating profile for a beach with no curated spot.

    Marked `precision: "region"` so the UI can label the rating as approximate.
    """
    activities = set(beach.get("activities") or [])
    sport = SPORT_WIND if (activities & _WIND_ACTIVITIES) and "Surfing" not in activities         else SPORT_SURF
    base = dict(_PROFILE_SHELTERED if _is_sheltered(beach) else _PROFILE_EXPOSED)
    base.update({
        "beach": beach["name"],
        "sport": sport,
        "shore_normal_deg": REGION_SHORE_NORMAL.get(beach.get("region"),
                                                    _DEFAULT_SHORE_NORMAL),
        "precision": "region",
    })
    if sport == SPORT_WIND:
        base["min_rideable_m"] = 0.0
        base["ideal_wind_kt"] = (13, 23)
    return base


def profile_for_beach(beach: dict) -> dict:
    """Rating profile for any beach: the curated spot if there is one, else derived.

    Takes a `beaches_data` beach dict (not just a name) because the derived
    path reads that beach's region and water_conditions.
    """
    spot = SPOTS_BY_BEACH.get(beach["name"])
    if spot is not None:
        return {**spot, "precision": "spot"}
    return derived_profile(beach)


def spot_for_beach(name: str) -> dict | None:
    """Return the curated spot record for a beach name, or None if uncurated."""
    return SPOTS_BY_BEACH.get(name)


def is_surf_spot(name: str) -> bool:
    """True when the beach is one of the named spots in SURF_SPOTS.

    Every beach can be *rated* (see `profile_for_beach`); this only says
    whether the beach is a recognised surf or kite destination, which is what
    the map's "surf spots only" filter means.
    """
    return name in SPOTS_BY_BEACH


def spot_points(curated_only: bool = False) -> list[tuple[str, float, float]]:
    """Return (beach_name, lat, lon) for beaches to fetch a forecast for.

    Coordinates come from `beaches_data` so there is exactly one source of
    truth for where each beach is. `curated_only=True` narrows to SURF_SPOTS.
    """
    from dashboard.beaches_data import BEACHES

    if curated_only:
        coords = {b["name"]: (b["latitude"], b["longitude"]) for b in BEACHES}
        out: list[tuple[str, float, float]] = []
        for spot in SURF_SPOTS:
            pos = coords.get(spot["beach"])
            if pos is None:   # guarded by tests/test_surf.py; skip rather than crash
                continue
            out.append((spot["beach"], pos[0], pos[1]))
        return out
    return [(b["name"], b["latitude"], b["longitude"]) for b in BEACHES]