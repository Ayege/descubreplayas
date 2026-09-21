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

"""Surf-condition scoring for Dominican Republic beaches.

WHAT THIS DOES
--------------
Turns a raw wave + wind forecast into a per-spot 0-10 score and a rating band
(flat / poor / fair / good / epic), the way a surf report does. The physics a
surfer actually cares about, in order of how much it decides the day:

1. SIZE       Is there enough swell for *this* spot, and not too much? A
              shallow reef works on 1 m; the same 1 m does nothing at a deep
              beach break, and 3 m closes both out.
2. DIRECTION  Swell arriving off-axis is shadowed by the coastline. Each spot
              has a shore normal and an exposure window (see surf_data.py).
3. PERIOD     Long-period groundswell (>10 s) arrives as organised lines;
              short-period windswell (<6 s) is disorganised slop of the same
              height.
4. WIND       Offshore wind (blowing from land to sea) holds the face up and
              grooms it. Onshore wind destroys it. Below ~3 kt it is glassy
              and direction stops mattering.

Score is the product of the four factors, so any one of them being terrible
sinks the day -- which is how surf actually works. Size and direction can each
take the score to zero; period and wind have floors (0.40 and 0.30) because
bad period or bad wind degrades a wave without erasing it.

Wind-sport spots (kitesurf / windsurf -- Cabarete, Kite Beach, Punta Popy and
the two east-coast lagoons) run a DIFFERENT scoring path: for them wind
strength is the resource rather than the spoiler, side-shore beats offshore
(offshore wind blows a kiter out to sea), and flat water is a feature.

HONEST LIMITS
-------------
These weights are a physically-grounded heuristic, not a model fitted to
observed surf reports -- nobody has validated them against what people found
when they got to the beach. They are deliberately transparent so they can be
tuned. The other real error source is `shore_normal_deg`, estimated to about
+/- 15 degrees per spot. Treat the score as "is it worth the drive", not as a
substitute for looking at the water.

DATA SOURCE
-----------
Open-Meteo Marine API (https://marine-api.open-meteo.com/v1/marine) -- free,
no API key, global wave model at ~0.08 deg. Wind comes from the Open-Meteo
Forecast API, already used by pipeline/ocean.py.

Wave/swell `*_direction` fields are the direction the waves come FROM
(standard wave-model convention, and confirmed against live values: the DR
north coast reads ~20 deg and the east coast ~70 deg, matching Atlantic
groundswell and trade-wind swell respectively). VERIFY against
https://open-meteo.com/en/docs/marine-weather-api if ratings ever look
mirrored.

The marine grid snaps a request to the nearest ocean cell, which for some
beaches is 10-20 km offshore. That is fine -- and correct -- for open-ocean
swell, but it means the sample is not literally at the beach; every returned
reading carries `sample_km` so the UI can say so.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Iterable, Optional

from dashboard.surf_data import (
    SPORT_SURF,
    SPORT_WIND,
    profile_for_beach,
    spot_for_beach,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rating bands
# ---------------------------------------------------------------------------
# Thresholds on the 0-10 score. 'good' is deliberately hard to reach and
# 'epic' rare -- a rating that says GOOD every day tells a visitor nothing.
RATING_BANDS: tuple[tuple[float, str], ...] = (
    (0.5, "flat"),
    (3.0, "poor"),
    (5.5, "fair"),
    (8.5, "good"),
    (10.1, "epic"),
)

# "unknown" is not a score band -- `rating_from_score` never returns it. It is
# what a rating carries when the forecast for that spot and hour is missing, so
# the UI can say "no data" instead of the unsupportable claim "flat".
RATING_ORDER = {"unknown": -1, "flat": 0, "poor": 1, "fair": 2, "good": 3, "epic": 4}

# Colours chosen to be distinct from the sargassum RISK_COLORS palette, so a
# surf badge and a risk badge are never mistaken for each other.
RATING_COLORS = {
    "flat": "#78909c",   # blue-grey  — nothing to ride
    "poor": "#ef6c00",   # amber
    "fair": "#fdd835",   # yellow
    "good": "#43a047",   # green
    "epic": "#00e5ff",   # cyan       — reserved for the rare day
    "unknown": "#455a64",  # dark slate — no forecast for this spot/hour
}

RATING_EMOJI = {"flat": "😴", "poor": "🙁", "fair": "🙂", "good": "🤙",
                "epic": "🔥", "unknown": "❔"}

RATING_LABELS = {
    "es": {"flat": "PLANO", "poor": "MALO", "fair": "REGULAR",
           "good": "BUENO", "epic": "ÉPICO", "unknown": "SIN DATOS"},
    "en": {"flat": "FLAT", "poor": "POOR", "fair": "FAIR",
           "good": "GOOD", "epic": "EPIC", "unknown": "NO DATA"},
}

# Reason codes, so the UI can say *why* without re-deriving the scoring.
REASON_LABELS = {
    "es": {
        "too_small":     "Oleaje muy pequeño aquí",
        "too_big":       "Muy grande, se cierra",
        "off_axis":      "Oleaje fuera de ángulo",
        "short_period":  "Oleaje corto y desordenado",
        "onshore_wind":  "Viento entrante desordena la ola",
        "cross_wind":    "Viento cruzado",
        "strong_offshore": "Terral muy fuerte",
        "clean":         "Oleaje limpio y ordenado",
        "glassy":        "Sin viento, agua de cristal",
        "no_wind":       "Sin viento suficiente",
        "too_windy":     "Demasiado viento",
        "offshore_unsafe": "Terral: peligroso para kite",
        "side_shore":    "Viento lateral, ideal",
        "ok":            "Condiciones utilizables",
        "no_data":       "Sin pronóstico de olas",
    },
    "en": {
        "too_small":     "Too small for this spot",
        "too_big":       "Too big, closing out",
        "off_axis":      "Swell off-angle here",
        "short_period":  "Short, messy windswell",
        "onshore_wind":  "Onshore wind blowing it out",
        "cross_wind":    "Cross-shore wind",
        "strong_offshore": "Strong offshore wind",
        "clean":         "Clean, organised swell",
        "glassy":        "No wind — glassy",
        "no_wind":       "Not enough wind",
        "too_windy":     "Overpowered",
        "offshore_unsafe": "Offshore — unsafe for kiting",
        "side_shore":    "Side-shore — ideal",
        "ok":            "Rideable conditions",
        "no_data":       "No wave forecast",
    },
}

# Below this wind speed the surface is glassy and wind direction is irrelevant.
GLASSY_KT = 3.0
# Wind speed at which direction carries its full weight.
WIND_FULL_WEIGHT_KT = 20.0

_M_TO_FT = 3.28084


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def angular_diff(a: float, b: float) -> float:
    """Smallest absolute angle between two compass bearings, 0..180 degrees."""
    d = abs((float(a) - float(b)) % 360.0)
    return 360.0 - d if d > 180.0 else d


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def compass_point(deg: float) -> str:
    """Nearest 16-point compass abbreviation for a bearing ('NNE', 'ESE', ...)."""
    points = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return points[int((float(deg) % 360.0) / 22.5 + 0.5) % 16]


def wind_relation(wind_from_deg: float, shore_normal_deg: float) -> str:
    """Classify wind relative to the shore: offshore / cross / onshore.

    `wind_from_deg` is the meteorological direction the wind blows FROM.
    A wind coming from the same bearing as the shore normal is blowing sea ->
    land (onshore); one coming from the opposite bearing is land -> sea
    (offshore), which is what grooms a wave face.
    """
    rel = angular_diff(wind_from_deg, shore_normal_deg)
    if rel >= 135.0:
        return "offshore"
    if rel >= 90.0:
        return "cross-offshore"
    if rel >= 45.0:
        return "cross-onshore"
    return "onshore"


# ---------------------------------------------------------------------------
# Individual scoring factors — each returns 0..1
# ---------------------------------------------------------------------------
def size_score(swell_m: float, spot: dict) -> tuple[float, bool]:
    """Score the swell height against this spot's rideable window.

    Returns (score, oversize). `oversize` is True when the swell is above the
    spot's manageable limit, which the caller must report differently from a
    zero score caused by flatness: "too big" and "too small" are both a 0 but
    a visitor needs to know which.
    """
    if swell_m is None:
        return 0.0, False
    swell = float(swell_m)
    mn = float(spot["min_rideable_m"])
    lo, hi = (float(v) for v in spot["ideal_m"])
    mx = float(spot["max_manageable_m"])

    if swell <= mn:
        return 0.0, False
    if swell < lo:
        return _clamp((swell - mn) / (lo - mn)), False
    if swell <= hi:
        return 1.0, False
    if swell >= mx:
        return 0.0, True
    # Between ideal and unmanageable: decays linearly to zero.
    return _clamp(1.0 - (swell - hi) / (mx - hi)), False


# Piecewise-linear period quality. Floors at 0.40 rather than 0 because even
# 4-second slop is still (marginally) surfable at the right size.
_PERIOD_CURVE = ((4.0, 0.40), (6.0, 0.60), (8.0, 0.80), (10.0, 0.95), (12.0, 1.00))


def period_score(period_s: float | None) -> float:
    """Score swell period: short windswell is slop, long groundswell is clean.

    A missing period is scored at the middle of the curve rather than
    optimistically or pessimistically -- `score_surf` decides separately
    whether the forecast is too incomplete to rate at all.
    """
    if period_s is None:
        return 0.6
    p = float(period_s)
    if p <= _PERIOD_CURVE[0][0]:
        return _PERIOD_CURVE[0][1]
    if p >= _PERIOD_CURVE[-1][0]:
        return _PERIOD_CURVE[-1][1]
    for (p0, s0), (p1, s1) in zip(_PERIOD_CURVE, _PERIOD_CURVE[1:]):
        if p <= p1:
            return s0 + (s1 - s0) * (p - p0) / (p1 - p0)
    return _PERIOD_CURVE[-1][1]


def swell_direction_score(swell_from_deg: float, spot: dict) -> float:
    """Score how squarely the swell hits this beach.

    Full marks on-axis, tapering to 0.35 at the edge of the spot's exposure
    window and to 0 a further 30 degrees outside it (fully coast-shadowed).
    """
    off = angular_diff(swell_from_deg, spot["shore_normal_deg"])
    half = float(spot["swell_window_deg"]) / 2.0
    if half <= 0:
        return 0.0
    if off >= half:
        # Outside the window — decays from the edge value to zero over 30 deg.
        return 0.35 * _clamp(1.0 - (off - half) / 30.0)
    return 0.35 + 0.65 * math.cos(math.radians(90.0 * off / half))


# Directional quality of wind for SURFING, by wind_relation() class.
_SURF_WIND_QUALITY = {
    "offshore": 1.00,
    "cross-offshore": 0.85,
    "cross-onshore": 0.55,
    "onshore": 0.30,
}


def wind_score_surf(speed_kt: float, wind_from_deg: float, spot: dict) -> float:
    """Score wind for surfing: offshore grooms, onshore ruins, calm is glassy.

    Light wind is scored near-perfect whatever its direction -- at 4 kt an
    onshore breeze does nothing to the wave. Direction only reaches full
    weight around 20 kt. A very strong offshore is penalised too: above ~22 kt
    it holds the wave up until it is unmakeable.
    """
    speed = max(0.0, float(speed_kt))
    relation = wind_relation(wind_from_deg, spot["shore_normal_deg"])
    quality = _SURF_WIND_QUALITY[relation]

    # Blend between "glassy, direction irrelevant" and "direction is decisive".
    weight = _clamp((speed - GLASSY_KT) / (WIND_FULL_WEIGHT_KT - GLASSY_KT))
    score = (1.0 - weight) + quality * weight

    if relation == "offshore" and speed > 22.0:
        score *= max(0.45, 1.0 - (speed - 22.0) / 30.0)
    return _clamp(score, 0.0, 1.0)


def wind_strength_score(speed_kt: float, spot: dict) -> float:
    """Score wind STRENGTH for kitesurf / windsurf, against the spot's window."""
    speed = max(0.0, float(speed_kt))
    lo, hi = (float(v) for v in spot.get("ideal_wind_kt", (14.0, 25.0)))
    floor = lo * 0.6                      # below this there is nothing to ride

    if speed <= floor:
        return 0.0
    if speed < lo:
        return _clamp((speed - floor) / (lo - floor))
    if speed <= hi:
        return 1.0
    # Overpowered: usable but degrading, and genuinely unsafe well above.
    return max(0.15, 1.0 - 0.65 * (speed - hi) / 10.0)


def wind_direction_score_kite(wind_from_deg: float, spot: dict) -> float:
    """Score wind DIRECTION for kitesurf / windsurf.

    Deliberately the near-inverse of the surf case. Side-shore is ideal;
    straight onshore is awkward to launch and land in; straight OFFSHORE is
    the dangerous one, because a rider who loses power is blown out to sea.
    """
    rel = angular_diff(wind_from_deg, spot["shore_normal_deg"])
    if 60.0 <= rel <= 120.0:
        return 1.00      # side-shore
    if 30.0 <= rel < 60.0 or 120.0 < rel <= 150.0:
        return 0.90      # side-onshore / side-offshore
    if rel < 30.0:
        return 0.70      # straight onshore
    return 0.35          # straight offshore — unsafe


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def rating_from_score(score: float) -> str:
    """Map a 0-10 score to a rating band key."""
    for threshold, label in RATING_BANDS:
        if float(score) < threshold:
            return label
    return "epic"


def face_height_ft(swell_m: float, spot: dict) -> tuple[float, float]:
    """Estimated breaking-face height range in feet, for display only.

    Offshore significant wave height is not what a surfer sees break. This
    applies the spot's shoaling `face_factor` and a +/-15% spread, which is
    how surf reports quote a range rather than a single number. It never feeds
    the score.
    """
    if swell_m is None:
        return 0.0, 0.0
    base = max(0.0, float(swell_m)) * float(spot.get("face_factor", 1.1)) * _M_TO_FT
    return round(base * 0.85, 1), round(base * 1.15, 1)


def rating_label(rating: str, lang: str = "es") -> str:
    """Localised rating word ('BUENO' / 'GOOD')."""
    return RATING_LABELS.get(lang, RATING_LABELS["es"]).get(rating, rating.upper())


def reason_label(reason: str, lang: str = "es") -> str:
    """Localised one-line explanation for a reason code."""
    return REASON_LABELS.get(lang, REASON_LABELS["es"]).get(reason, "")


# ---------------------------------------------------------------------------
# Whole-spot scoring
# ---------------------------------------------------------------------------
def _surf_reason(size: float, oversize: bool, direction: float,
                 period: float, wind: float, relation: str, speed_kt: float) -> str:
    """Pick the single most decisive factor, for a one-line explanation."""
    if oversize:
        return "too_big"
    if size <= 0.0:
        return "too_small"
    if direction < 0.35:
        return "off_axis"
    # Whichever quality factor is dragging the day down the most.
    if wind <= period and wind < 0.7:
        if relation == "onshore":
            return "onshore_wind"
        if relation == "offshore":
            return "strong_offshore"
        return "cross_wind"
    if period < 0.7:
        return "short_period"
    if speed_kt < GLASSY_KT:
        return "glassy"
    if period >= 0.9 and wind >= 0.85:
        return "clean"
    return "ok"


def _no_data_result(sport: str) -> dict:
    """The rating returned when the essential forecast fields are missing."""
    return {
        "sport": sport,
        "score": 0.0,
        "rating": "unknown",
        "reason": "no_data",
        "wind_relation": "unknown",
        "factors": {},
        "face_ft": (0.0, 0.0),
        "oversize": False,
        "no_data": True,
    }


def score_surf(spot: dict, cond: dict) -> dict:
    """Score a surf spot. See `rate_spot` for the returned shape."""
    swell_m = cond.get("swell_height_m")
    period_s = cond.get("swell_period_s")
    swell_dir = cond.get("swell_direction_deg")
    speed_kt = cond.get("wind_speed_kt") or 0.0
    wind_dir = cond.get("wind_direction_deg")

    # Swell height is the one field a surf rating cannot be invented without.
    if swell_m is None:
        return _no_data_result(SPORT_SURF)

    size, oversize = size_score(swell_m, spot)
    direction = swell_direction_score(swell_dir, spot) if swell_dir is not None else 0.7
    period = period_score(period_s) if period_s is not None else 0.6
    if wind_dir is None:
        wind, relation = 0.8, "unknown"
    else:
        wind = wind_score_surf(speed_kt, wind_dir, spot)
        relation = wind_relation(wind_dir, spot["shore_normal_deg"])

    score = 10.0 * size * direction * period * wind
    rating = rating_from_score(score)
    # A too-big day is not a flat day. Floor it at 'poor' so the badge and the
    # reason agree: the water is unrideable, but there is plenty of it.
    if oversize and rating == "flat":
        rating = "poor"

    return {
        "sport": SPORT_SURF,
        "score": round(score, 1),
        "rating": rating,
        "reason": _surf_reason(size, oversize, direction, period, wind, relation, speed_kt),
        "wind_relation": relation,
        "factors": {
            "size": round(size, 2),
            "direction": round(direction, 2),
            "period": round(period, 2),
            "wind": round(wind, 2),
        },
        "face_ft": face_height_ft(swell_m, spot),
        "oversize": oversize,
        "no_data": False,
    }


def _wind_reason(spot: dict, speed_kt: float, strength: float,
                 direction: float, oversize: bool) -> str:
    """Pick the single most decisive factor for a wind-sport day.

    `wind_strength_score` deliberately returns a low value at both ends of its
    curve -- not enough wind, and far too much -- so the raw strength alone
    cannot tell those apart. The speed is compared against the spot's window
    to say which end it is.
    """
    lo, hi = (float(v) for v in spot.get("ideal_wind_kt", (14.0, 25.0)))
    if strength <= 0.0:
        return "no_wind"
    if direction <= 0.4:
        return "offshore_unsafe"
    if speed_kt > hi:
        return "too_windy"
    if speed_kt < lo:
        return "no_wind"           # above the floor but below the usable window
    if oversize:
        return "too_big"
    if direction >= 1.0 and strength >= 0.9:
        return "side_shore"
    return "ok"


def score_wind(spot: dict, cond: dict) -> dict:
    """Score a kitesurf / windsurf spot. See `rate_spot` for the shape."""
    speed_kt = cond.get("wind_speed_kt")
    wind_dir = cond.get("wind_direction_deg")
    swell_m = cond.get("swell_height_m") or 0.0

    # Wind speed is to a kite spot what swell height is to a surf break.
    if speed_kt is None:
        return _no_data_result(SPORT_WIND)

    strength = wind_strength_score(speed_kt, spot)
    if wind_dir is None:
        direction, relation = 0.85, "unknown"
    else:
        direction = wind_direction_score_kite(wind_dir, spot)
        relation = wind_relation(wind_dir, spot["shore_normal_deg"])

    # Big swell at a flat-water spot is a hazard, not a bonus.
    oversize = float(swell_m) > float(spot["max_manageable_m"])
    wave_factor = 0.5 if oversize else 1.0

    score = 10.0 * strength * direction * wave_factor
    rating = rating_from_score(score)

    # 'flat' means "no wind" here, which the reason code already conveys; an
    # overpowered day must not read the same as a windless one.
    if rating == "flat" and strength > 0.0:
        rating = "poor"

    return {
        "sport": SPORT_WIND,
        "score": round(score, 1),
        "rating": rating,
        "reason": _wind_reason(spot, speed_kt, strength, direction, oversize),
        "wind_relation": relation,
        "factors": {
            "strength": round(strength, 2),
            "direction": round(direction, 2),
            "waves": round(wave_factor, 2),
        },
        "face_ft": face_height_ft(swell_m, spot),
        "oversize": oversize,
        "no_data": False,
    }


def rate_spot(spot: dict, cond: dict) -> dict:
    """Rate one spot under one set of conditions.

    Args:
        spot: a record from `dashboard.surf_data.SURF_SPOTS`.
        cond: a conditions dict as produced by `fetch_surf_conditions` —
            swell_height_m, swell_period_s, swell_direction_deg,
            wind_speed_kt, wind_direction_deg. Missing keys fall back to
            neutral values rather than raising, so a partial forecast still
            produces a usable (if less certain) rating.

    Returns:
        {
          "sport": "surf" | "wind",
          "score": 0.0-10.0,
          "rating": "flat"|"poor"|"fair"|"good"|"epic",
          "reason": reason code for REASON_LABELS,
          "wind_relation": "offshore"|"cross-offshore"|"cross-onshore"|"onshore",
          "factors": {...},              # per-factor 0-1, for transparency
          "face_ft": (lo, hi),           # display-only breaking-face estimate
          "oversize": bool,
          "no_data": bool,               # True when the forecast was missing;
                                         # rating is then "unknown", not "flat"
          "precision": "spot" | "region",  # how well this beach is calibrated
        }
    """
    rated = score_wind(spot, cond) if spot.get("sport") == SPORT_WIND \
        else score_surf(spot, cond)
    rated["precision"] = spot.get("precision", "spot")
    return rated


def rate_beach(beach: dict | str, cond: dict) -> Optional[dict]:
    """Rate any beach under one set of conditions.

    Accepts a `beaches_data` beach dict, in which case an uncurated beach is
    rated from its region's coastal orientation (see
    `surf_data.profile_for_beach`) and the result carries
    `precision: "region"`.

    A bare beach name is also accepted for convenience, but then only the 13
    curated spots can be resolved -- there is no region or water_conditions to
    derive a profile from -- and anything else returns None.
    """
    if isinstance(beach, str):
        spot = spot_for_beach(beach)
        if spot is None:
            return None
        profile = {**spot, "precision": "spot"}
    else:
        profile = profile_for_beach(beach)
    rated = rate_spot(profile, cond)
    rated["precision"] = profile.get("precision", "spot")
    return rated


# ---------------------------------------------------------------------------
# Forecast fetch (Open-Meteo: marine + wind, one batched request each)
# ---------------------------------------------------------------------------
MARINE_BASE_URL = "https://marine-api.open-meteo.com/v1/marine"
WIND_BASE_URL = "https://api.open-meteo.com/v1/forecast"

# Only what the score and the UI actually consume. Total wave height, wave
# period, wave direction, wind-wave height and gusts were all being fetched
# and parsed without ever being read.
MARINE_VARS = (
    "swell_wave_height",
    "swell_wave_period",
    "swell_wave_direction",
)
WIND_VARS = ("wind_speed_10m", "wind_direction_10m")

# Separate connect and read timeouts, and the connect one is deliberately
# short. marine-api.open-meteo.com publishes an AAAA record that is
# unreachable from some networks; urllib3 tries addresses in resolver order,
# so it would sit on the dead IPv6 address for the FULL timeout before falling
# back to IPv4 -- a measured 30s stall on every cold fetch, which blocked the
# whole page. A 1.5s connect timeout makes that fallback almost immediate and
# costs nothing on a network where IPv6 works, because the first connect then
# succeeds outright. (curl does not show this: it implements Happy Eyeballs.)
CONNECT_TIMEOUT = 1.5
READ_TIMEOUT = 15.0

_EARTH_R_KM = 6371.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_R_KM * math.asin(math.sqrt(a))


def _get_json(url: str, params: dict):
    """GET JSON with the repo's certifi-pinned requests setup."""
    import certifi
    import requests

    resp = requests.get(url, params=params, verify=certifi.where(),
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    resp.raise_for_status()
    return resp.json()


def _as_list(payload) -> list:
    """Open-Meteo returns a bare object for one location, a list for many."""
    return payload if isinstance(payload, list) else [payload]


def _pick(series: list | None, i: int):
    """Value at index i, or None when the series is missing or short."""
    if not series or i >= len(series):
        return None
    v = series[i]
    return None if v is None else float(v)


def fetch_surf_conditions(
    points: Iterable[tuple[str, float, float]],
    forecast_days: int = 3,
) -> dict[str, list[dict]]:
    """Fetch hourly wave + wind series for a list of (name, lat, lon) spots.

    Two batched requests total -- one to the marine API and one to the wind
    API -- rather than one pair per spot, following the same batching approach
    as `pipeline.ocean._fetch_open_meteo_wind_batch`.

    Both calls are independently guarded: if the wind request fails the wave
    data is still returned (with wind fields None, which `rate_spot` treats as
    neutral), and if the wave request fails the result is empty and the caller
    falls back to showing no rating. One failing step never raises into the UI.

    Returns:
        {beach_name: [conditions, ...]} ordered by time, where each
        conditions dict has: time (aware UTC datetime), swell_height_m,
        swell_period_s, swell_direction_deg, wind_speed_kt,
        wind_direction_deg, and sample_km (distance from the beach to the
        ocean grid cell actually used).
    """
    pts = [(str(n), float(la), float(lo)) for n, la, lo in points]
    if not pts:
        return {}

    lats = ",".join(str(la) for _, la, _ in pts)
    lons = ",".join(str(lo) for _, _, lo in pts)

    try:
        marine = _as_list(_get_json(MARINE_BASE_URL, {
            "latitude": lats,
            "longitude": lons,
            "hourly": ",".join(MARINE_VARS),
            "forecast_days": forecast_days,
            "timezone": "UTC",
        }))
    except Exception:
        logger.warning("Open-Meteo marine fetch failed; no surf ratings this run.",
                       exc_info=True)
        return {}

    try:
        wind = _as_list(_get_json(WIND_BASE_URL, {
            "latitude": lats,
            "longitude": lons,
            "hourly": ",".join(WIND_VARS),
            "wind_speed_unit": "kn",
            "forecast_days": forecast_days,
            "timezone": "UTC",
        }))
    except Exception:
        logger.warning("Open-Meteo wind fetch failed; rating waves without wind.",
                       exc_info=True)
        wind = []

    out: dict[str, list[dict]] = {}
    for i, (name, lat, lon) in enumerate(pts):
        if i >= len(marine):
            continue
        m_hourly = (marine[i] or {}).get("hourly") or {}
        times_raw = m_hourly.get("time") or []
        if not times_raw:
            continue

        w_hourly = (wind[i] or {}).get("hourly") if i < len(wind) else None
        w_hourly = w_hourly or {}
        # The wind API has its own grid, so its timestamps are matched by
        # value rather than by index.
        w_index = {t: j for j, t in enumerate(w_hourly.get("time") or [])}

        # How far the ocean grid cell the model actually used is from the beach.
        m_lat = (marine[i] or {}).get("latitude", lat)
        m_lon = (marine[i] or {}).get("longitude", lon)
        sample_km = round(_haversine_km(lat, lon, float(m_lat), float(m_lon)), 1)

        series: list[dict] = []
        for k, ts in enumerate(times_raw):
            j = w_index.get(ts)
            series.append({
                "time": dt.datetime.fromisoformat(ts).replace(tzinfo=dt.timezone.utc),
                "swell_height_m": _pick(m_hourly.get("swell_wave_height"), k),
                "swell_period_s": _pick(m_hourly.get("swell_wave_period"), k),
                "swell_direction_deg": _pick(m_hourly.get("swell_wave_direction"), k),
                "wind_speed_kt": _pick(w_hourly.get("wind_speed_10m"), j) if j is not None else None,
                "wind_direction_deg": _pick(w_hourly.get("wind_direction_10m"), j) if j is not None else None,
                "sample_km": sample_km,
            })
        out[name] = series

    logger.info("Surf conditions fetched for %d/%d spot(s).", len(out), len(pts))
    return out


# ---------------------------------------------------------------------------
# Time-series reducers
# ---------------------------------------------------------------------------
def conditions_at(series: list[dict], when: dt.datetime | None = None) -> Optional[dict]:
    """Conditions entry nearest a given time (default: now, UTC)."""
    if not series:
        return None
    target = when or dt.datetime.now(dt.timezone.utc)
    if target.tzinfo is None:
        target = target.replace(tzinfo=dt.timezone.utc)
    return min(series, key=lambda c: abs((c["time"] - target).total_seconds()))


def daylight_window(series: list[dict], day: dt.date,
                    start_hour: int = 10, end_hour: int = 22) -> list[dict]:
    """Entries on `day` within surfable daylight hours.

    Hours are UTC; the DR is UTC-4 year-round (no daylight saving), so the
    default 10:00-22:00 UTC is 06:00-18:00 local -- dawn to dusk, which is
    when anyone is actually in the water.
    """
    return [c for c in series
            if c["time"].date() == day and start_hour <= c["time"].hour <= end_hour]


def best_of(spot: dict, entries: list[dict]) -> Optional[dict]:
    """Highest-scoring rating across a set of conditions, with its hour.

    Returns the `rate_spot` dict plus an "at" key holding that entry's time
    and a "conditions" key holding the entry it came from.
    """
    if not entries:
        return None
    best: Optional[dict] = None
    for cond in entries:
        rated = rate_spot(spot, cond)
        if best is None or rated["score"] > best["score"]:
            rated["at"] = cond["time"]
            rated["conditions"] = cond
            best = rated
    return best


def daily_outlook(spot: dict, series: list[dict], days: int = 3) -> list[dict]:
    """Best daylight rating per day, for a short forecast strip in the UI."""
    if not series:
        return []
    out: list[dict] = []
    first_day = series[0]["time"].date()
    for offset in range(days):
        day = first_day + dt.timedelta(days=offset)
        best = best_of(spot, daylight_window(series, day))
        if best is not None:
            best["day"] = day
            out.append(best)
    return out
