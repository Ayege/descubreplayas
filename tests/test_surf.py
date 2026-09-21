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

"""Offline tests for the surf-rating engine and spot dataset.

Every assertion here runs without network access. Run from the repo root:

    python -m tests.test_surf

To additionally hit the live Open-Meteo marine + wind APIs (not part of the
offline suite, so a vendor outage never fails CI):

    python -m tests.test_surf --live
"""
from __future__ import annotations

import datetime as dt
import sys

from dashboard.beaches_data import BEACHES
from dashboard.surf_data import (
    SPORT_SURF,
    SPORT_WIND,
    SURF_SPOTS,
    is_surf_spot,
    profile_for_beach,
    spot_for_beach,
    spot_points,
)
from dashboard.surf import (
    RATING_LABELS,
    RATING_ORDER,
    REASON_LABELS,
    angular_diff,
    compass_point,
    conditions_at,
    daily_outlook,
    daylight_window,
    face_height_ft,
    fetch_surf_conditions,
    period_score,
    rate_beach,
    rate_spot,
    rating_from_score,
    size_score,
    swell_direction_score,
    wind_direction_score_kite,
    wind_relation,
    wind_score_surf,
    wind_strength_score,
)

# A spot used throughout as the reference surf break: north-facing reef,
# shore normal 20 deg, ideal swell 1.0-2.4 m.
ENCUENTRO = spot_for_beach("Playa Encuentro")
CABARETE = spot_for_beach("Cabarete Beach")


def _cond(**kw) -> dict:
    """Build a conditions dict with sane defaults, overridden by kwargs."""
    base = {
        "swell_height_m": 1.2,
        "swell_period_s": 9.0,
        "swell_direction_deg": 20.0,
        "wind_speed_kt": 8.0,
        "wind_direction_deg": 200.0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Dataset integrity
# ---------------------------------------------------------------------------
def test_dataset_joins_to_beaches() -> None:
    """Every spot must name a beach that exists in beaches_data."""
    names = {b["name"] for b in BEACHES}
    for spot in SURF_SPOTS:
        assert spot["beach"] in names, f"Unknown beach: {spot['beach']!r}"
    assert len(spot_points(curated_only=True)) == len(SURF_SPOTS), \
        "Some spot lost its coordinates"
    assert len(spot_points()) == len(BEACHES), "Fetch list must cover every beach"
    assert len({s["beach"] for s in SURF_SPOTS}) == len(SURF_SPOTS), "Duplicate spot"
    print(f"  {len(SURF_SPOTS)} spots, all joined to beaches_data: OK")


def test_dataset_fields_are_coherent() -> None:
    required = ("sport", "shore_normal_deg", "swell_window_deg", "min_rideable_m",
                "ideal_m", "max_manageable_m", "face_factor")
    # surf_data.py is calibration only. Any descriptive prose creeping back in
    # would be static info the surf section has no way to display.
    forbidden = ("break_type", "skill", "best_season", "best_wind",
                 "note_en", "note_es", "description")
    for spot in SURF_SPOTS:
        name = spot["beach"]
        for field in required:
            assert field in spot, f"{name}: missing {field}"
        for field in forbidden:
            assert field not in spot, \
                f"{name}: {field} is static prose — the UI shows live data only"
        assert spot["sport"] in (SPORT_SURF, SPORT_WIND), f"{name}: bad sport"
        assert 0 <= spot["shore_normal_deg"] < 360, f"{name}: shore normal out of range"
        assert 60 <= spot["swell_window_deg"] <= 180, f"{name}: implausible window"

        lo, hi = spot["ideal_m"]
        assert lo < hi < spot["max_manageable_m"], \
            f"{name}: size window not ordered lo<hi<max"
        if spot["sport"] == SPORT_SURF:
            assert spot["min_rideable_m"] < lo, \
                f"{name}: a surf spot needs a minimum swell below its ideal"
        else:
            # A kite lagoon is at its best flat, so it has no minimum swell.
            assert spot["min_rideable_m"] == 0.0, \
                f"{name}: wind spots must have min_rideable_m == 0"
        # Shoaling can amplify a wave but not double it; a sheltered bay damps.
        assert 0.8 <= spot["face_factor"] <= 1.4, f"{name}: implausible face_factor"
        if spot["sport"] == SPORT_WIND:
            assert "ideal_wind_kt" in spot, f"{name}: wind spot needs ideal_wind_kt"
            wlo, whi = spot["ideal_wind_kt"]
            assert 8 <= wlo < whi <= 35, f"{name}: implausible wind window"
    print("  field ranges + size/wind windows coherent: OK")


def test_lookup_helpers() -> None:
    assert is_surf_spot("Playa Encuentro")
    assert not is_surf_spot("Playa Bávaro"), "A calm resort beach is not a named spot"
    assert spot_for_beach("nope") is None
    # By NAME, only the curated spots resolve — there is no region to derive from.
    assert rate_beach("Playa Bávaro", _cond()) is None
    assert rate_beach("Playa Encuentro", _cond())["precision"] == "spot"
    print("  spot lookup by name: OK")


def test_every_beach_gets_a_rating() -> None:
    """Passing a beach dict rates all 56 beaches, not just the 13 named spots."""
    by_name = {b["name"]: b for b in BEACHES}
    precisions = {"spot": 0, "region": 0}
    for beach in BEACHES:
        rated = rate_beach(beach, _cond())
        assert rated is not None, f"{beach['name']}: no rating"
        assert rated["precision"] in precisions, f"{beach['name']}: bad precision"
        assert 0.0 <= rated["score"] <= 10.0
        assert rated["rating"] in RATING_ORDER
        precisions[rated["precision"]] += 1
    assert precisions["spot"] == len(SURF_SPOTS), \
        f"Curated count drifted: {precisions['spot']} vs {len(SURF_SPOTS)}"
    assert precisions["region"] == len(BEACHES) - len(SURF_SPOTS)

    # A profile must be derivable from region + water_conditions alone.
    for beach in BEACHES:
        prof = profile_for_beach(beach)
        assert 0 <= prof["shore_normal_deg"] < 360
        assert prof["min_rideable_m"] <= prof["ideal_m"][0] < prof["ideal_m"][1]

    # Physical sanity: a 1.5 m N-swell at 10 s must rate the north-facing reef
    # far above a south-coast Caribbean beach, which the coast shadows entirely.
    north = rate_beach(by_name["Playa Encuentro"], _cond(
        swell_height_m=1.5, swell_period_s=10.0, swell_direction_deg=15.0))
    south = rate_beach(by_name["Boca Chica Beach"], _cond(
        swell_height_m=1.5, swell_period_s=10.0, swell_direction_deg=15.0))
    assert north["score"] > south["score"] + 5, \
        f"North swell: Encuentro {north['score']} vs Boca Chica {south['score']}"
    print(f"  all {len(BEACHES)} beaches rated "
          f"({precisions['spot']} curated, {precisions['region']} region-derived): OK")


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def test_angular_diff_wraps() -> None:
    assert angular_diff(10, 350) == 20, "Must wrap across north"
    assert angular_diff(350, 10) == 20
    assert angular_diff(0, 180) == 180
    assert angular_diff(90, 270) == 180
    assert angular_diff(45, 45) == 0
    assert angular_diff(-10, 10) == 20, "Negative bearings must normalise"
    print("  angular_diff wrap-around: OK")


def test_compass_point() -> None:
    for deg, want in ((0, "N"), (20, "NNE"), (90, "E"), (180, "S"), (359, "N")):
        got = compass_point(deg)
        assert got == want, f"{deg} deg -> {got}, expected {want}"
    print("  compass_point: OK")


def test_wind_relation_for_north_facing_beach() -> None:
    """Shore normal 20 deg: wind FROM ~20 is onshore, FROM ~200 is offshore."""
    n = ENCUENTRO["shore_normal_deg"]
    assert wind_relation(20, n) == "onshore", "Wind from the sea is onshore"
    assert wind_relation(200, n) == "offshore", "Wind from the land is offshore"
    assert wind_relation(110, n) == "cross-offshore"
    assert wind_relation(70, n) == "cross-onshore"
    print("  wind_relation onshore/offshore/cross: OK")


# ---------------------------------------------------------------------------
# Individual factors
# ---------------------------------------------------------------------------
def test_size_score_window() -> None:
    small, over_small = size_score(0.2, ENCUENTRO)      # below min_rideable 0.5
    assert small == 0.0 and not over_small, "Tiny swell scores 0, not oversize"

    ideal, over_ideal = size_score(1.6, ENCUENTRO)      # inside ideal 1.0-2.4
    assert ideal == 1.0 and not over_ideal

    ramp, _ = size_score(0.75, ENCUENTRO)               # between min and ideal
    assert 0.0 < ramp < 1.0, f"Expected a partial ramp, got {ramp}"

    decay, over_decay = size_score(3.2, ENCUENTRO)      # between ideal and max 4.0
    assert 0.0 < decay < 1.0 and not over_decay

    huge, over_huge = size_score(6.0, ENCUENTRO)        # above max_manageable
    assert huge == 0.0 and over_huge, "Above max must flag oversize, not just 0"
    print("  size_score window + oversize flag: OK")


def test_period_score_is_monotonic_and_bounded() -> None:
    periods = [2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 20]
    scores = [period_score(p) for p in periods]
    for a, b in zip(scores, scores[1:]):
        assert b >= a, f"period_score must not decrease: {a} -> {b}"
    assert all(0.40 <= s <= 1.0 for s in scores), "Period score must stay in [0.40, 1]"
    assert period_score(4) == 0.40 and period_score(12) == 1.00
    assert period_score(11) > period_score(5), "Groundswell must beat windswell"
    print("  period_score monotonic in [0.40, 1.00]: OK")


def test_swell_direction_score_tapers_off_axis() -> None:
    on_axis = swell_direction_score(20, ENCUENTRO)          # == shore normal
    edge = swell_direction_score(20 + 75, ENCUENTRO)        # window half-width
    outside = swell_direction_score(20 + 75 + 40, ENCUENTRO)

    assert abs(on_axis - 1.0) < 1e-9, f"On-axis must be 1.0, got {on_axis}"
    assert abs(edge - 0.35) < 1e-6, f"Window edge should be 0.35, got {edge}"
    assert outside == 0.0, "Well outside the window must be fully shadowed"
    # Monotonic decay across the window.
    walk = [swell_direction_score(20 + off, ENCUENTRO) for off in range(0, 130, 10)]
    for a, b in zip(walk, walk[1:]):
        assert b <= a + 1e-9, "Direction score must decay away from the normal"
    # Symmetric either side of the normal.
    assert abs(swell_direction_score(50, ENCUENTRO)
               - swell_direction_score(350, ENCUENTRO)) < 1e-9, "Must be symmetric"
    print("  swell_direction_score taper + symmetry: OK")


def test_wind_score_surf_prefers_offshore() -> None:
    n = ENCUENTRO["shore_normal_deg"]
    glassy = wind_score_surf(1.0, 20, ENCUENTRO)        # 1 kt onshore
    assert glassy > 0.95, "Under ~3 kt direction should barely matter"

    offshore = wind_score_surf(15, 200, ENCUENTRO)
    onshore = wind_score_surf(15, 20, ENCUENTRO)
    cross = wind_score_surf(15, 110, ENCUENTRO)
    assert offshore > cross > onshore, \
        f"offshore {offshore} > cross {cross} > onshore {onshore}"

    # Direction matters more as the wind builds.
    assert wind_score_surf(25, 20, ENCUENTRO) < wind_score_surf(8, 20, ENCUENTRO), \
        "A stronger onshore must score worse than a light one"
    # A howling offshore holds the wave up and is penalised too.
    assert wind_score_surf(35, 200, ENCUENTRO) < wind_score_surf(12, 200, ENCUENTRO), \
        "Very strong offshore should not beat a light offshore"
    assert wind_relation(200, n) == "offshore"
    print("  wind_score_surf offshore > cross > onshore, gales penalised: OK")


def test_wind_strength_and_direction_for_kiting() -> None:
    lo, hi = CABARETE["ideal_wind_kt"]
    assert wind_strength_score(4, CABARETE) == 0.0, "No wind, no kiting"
    assert wind_strength_score((lo + hi) / 2, CABARETE) == 1.0, "Mid-window is ideal"
    assert 0.0 < wind_strength_score(lo * 0.8, CABARETE) < 1.0, "Should ramp in"
    assert wind_strength_score(hi + 12, CABARETE) < 0.5, "Well overpowered"

    n = CABARETE["shore_normal_deg"]
    side = wind_direction_score_kite(n + 90, CABARETE)
    onshore = wind_direction_score_kite(n, CABARETE)
    offshore = wind_direction_score_kite(n + 180, CABARETE)
    assert side == 1.0, "Side-shore is the ideal kite direction"
    assert offshore < onshore < side, \
        "Offshore is the dangerous one and must rank below straight onshore"
    print("  kite wind strength window + side-shore preference: OK")


# ---------------------------------------------------------------------------
# End-to-end named scenarios
# ---------------------------------------------------------------------------
def test_classic_winter_groundswell_rates_high() -> None:
    """1.6 m at 11 s from the NNE with a light offshore = a great Encuentro day."""
    rated = rate_beach("Playa Encuentro", _cond(
        swell_height_m=1.6, swell_period_s=11.0, swell_direction_deg=25.0,
        wind_speed_kt=6.0, wind_direction_deg=200.0,
    ))
    assert rated["sport"] == SPORT_SURF
    assert rated["rating"] in ("good", "epic"), f"Got {rated['rating']} @ {rated['score']}"
    assert rated["wind_relation"] == "offshore"
    assert rated["reason"] == "clean", f"Expected 'clean', got {rated['reason']}"
    lo_ft, hi_ft = rated["face_ft"]
    assert 4.0 <= lo_ft < hi_ft <= 9.0, f"Implausible face range {lo_ft}-{hi_ft} ft"
    print(f"  winter groundswell: {rated['score']}/10 {rated['rating']} "
          f"({lo_ft}-{hi_ft} ft): OK")


def test_flat_summer_day_rates_flat() -> None:
    """0.4 m at 5 s is the DR north coast in September — nothing to ride."""
    rated = rate_beach("Playa Encuentro", _cond(
        swell_height_m=0.4, swell_period_s=5.3, swell_direction_deg=17.0,
        wind_speed_kt=5.0, wind_direction_deg=110.0,
    ))
    assert rated["rating"] == "flat", f"Got {rated['rating']} @ {rated['score']}"
    assert rated["reason"] == "too_small"
    assert rated["score"] == 0.0
    print("  flat summer day: OK")


def test_onshore_windswell_beats_nothing_but_is_not_good() -> None:
    """Right size, wrong everything else: short period + 18 kt onshore."""
    rated = rate_beach("Playa Encuentro", _cond(
        swell_height_m=1.2, swell_period_s=6.0, swell_direction_deg=40.0,
        wind_speed_kt=18.0, wind_direction_deg=30.0,
    ))
    assert rated["rating"] in ("poor", "fair"), f"Got {rated['rating']} @ {rated['score']}"
    assert rated["reason"] in ("onshore_wind", "short_period")
    print(f"  onshore windswell: {rated['score']}/10 {rated['rating']} "
          f"({rated['reason']}): OK")


def test_oversize_day_is_poor_not_flat() -> None:
    """A 6 m swell scores 0 for rideability but must not read the same as flat."""
    rated = rate_beach("Playa Grande", _cond(swell_height_m=6.0, swell_period_s=13.0))
    assert rated["oversize"] is True
    assert rated["rating"] == "poor", f"Oversize must floor at 'poor', got {rated['rating']}"
    assert rated["reason"] == "too_big"
    print("  oversize day reads 'too big', not 'flat': OK")


def test_off_axis_swell_is_shadowed() -> None:
    """Good-sized long-period swell from the south cannot reach a north coast."""
    rated = rate_beach("Playa Encuentro", _cond(
        swell_height_m=1.8, swell_period_s=12.0, swell_direction_deg=200.0,
    ))
    assert rated["reason"] == "off_axis", f"Got {rated['reason']}"
    assert rated["rating"] in ("flat", "poor")
    print("  south swell shadowed on a north-facing coast: OK")


def test_kite_spot_scenarios() -> None:
    """Cabarete: dead morning, classic afternoon, and an unsafe offshore."""
    morning = rate_beach("Cabarete Beach", {"wind_speed_kt": 5.0,
                                            "wind_direction_deg": 70.0,
                                            "swell_height_m": 0.6})
    afternoon = rate_beach("Cabarete Beach", {"wind_speed_kt": 20.0,
                                              "wind_direction_deg": 70.0,
                                              "swell_height_m": 0.8})
    offshore = rate_beach("Cabarete Beach", {"wind_speed_kt": 20.0,
                                             "wind_direction_deg": 205.0,
                                             "swell_height_m": 0.8})
    assert afternoon["sport"] == SPORT_WIND
    assert morning["rating"] == "flat" and morning["reason"] == "no_wind"
    assert afternoon["rating"] in ("good", "epic"), \
        f"Classic Cabarete afternoon rated {afternoon['rating']}"
    assert offshore["reason"] == "offshore_unsafe", \
        "Straight offshore wind must be called out as unsafe for kiting"
    assert offshore["score"] < afternoon["score"]
    print(f"  kite spot: morning flat, afternoon {afternoon['score']}/10, "
          f"offshore flagged unsafe: OK")


def test_surf_swell_does_not_inflate_a_kite_spot() -> None:
    """Big swell at a flat-water lagoon is a hazard, not a better rating."""
    calm = rate_beach("Playa Blanca", {"wind_speed_kt": 16.0,
                                       "wind_direction_deg": 100.0 + 90,
                                       "swell_height_m": 0.4})
    stormy = rate_beach("Playa Blanca", {"wind_speed_kt": 16.0,
                                         "wind_direction_deg": 100.0 + 90,
                                         "swell_height_m": 2.5})
    assert stormy["oversize"] is True
    assert stormy["score"] < calm["score"], "Oversize must reduce a flat-water score"
    print("  flat-water spot penalised by big swell: OK")


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------
def test_every_spot_rates_across_a_condition_sweep() -> None:
    """No spot may crash or leave the 0-10 / known-rating contract."""
    n = 0
    for spot in SURF_SPOTS:
        for swell in (0.0, 0.3, 0.8, 1.5, 2.5, 4.0, 7.0):
            for period in (3.0, 6.0, 9.0, 14.0):
                for wdir in (0, 45, 90, 135, 180, 270):
                    for wspd in (0.0, 8.0, 18.0, 30.0, 45.0):
                        rated = rate_spot(spot, _cond(
                            swell_height_m=swell, swell_period_s=period,
                            swell_direction_deg=(spot["shore_normal_deg"] + 10) % 360,
                            wind_speed_kt=wspd, wind_direction_deg=wdir,
                        ))
                        assert 0.0 <= rated["score"] <= 10.0, \
                            f"{spot['beach']}: score {rated['score']} out of range"
                        assert rated["rating"] in RATING_ORDER, \
                            f"{spot['beach']}: unknown rating {rated['rating']}"
                        assert rated["reason"] in REASON_LABELS["en"], \
                            f"{spot['beach']}: unknown reason {rated['reason']}"
                        assert rated["face_ft"][0] <= rated["face_ft"][1]
                        n += 1
    print(f"  {n} synthetic condition combinations across {len(SURF_SPOTS)} spots: OK")


def test_missing_fields_degrade_gracefully() -> None:
    """A partial forecast must produce a rating, not an exception.

    The marine grid genuinely returns nulls for some cells and hours, so this
    is a production path, not just a test convenience.
    """
    for spot in SURF_SPOTS:
        # Nothing at all: must say so rather than claim the spot is flat.
        empty = rate_spot(spot, {})
        assert empty["rating"] == "unknown", f"{spot['beach']}: {empty['rating']}"
        assert empty["reason"] == "no_data"
        assert empty["no_data"] is True

        # An explicit null is the same case as an absent key.
        nulls = rate_spot(spot, {"swell_height_m": None, "wind_speed_kt": None,
                                 "swell_period_s": None, "swell_direction_deg": None,
                                 "wind_direction_deg": None})
        assert nulls["rating"] == "unknown", f"{spot['beach']}: nulls must be no_data"

        # Waves but no wind: still ratable, and with no bogus onshore/offshore
        # call. Wind spots need the wind, so they stay unknown.
        partial = rate_spot(spot, {"swell_height_m": 1.4, "swell_period_s": 10.0,
                                   "swell_direction_deg": spot["shore_normal_deg"]})
        assert partial["wind_relation"] == "unknown"
        assert 0.0 <= partial["score"] <= 10.0
        if spot["sport"] == SPORT_SURF:
            assert partial["no_data"] is False, "Waves alone are enough to rate surf"
        else:
            assert partial["rating"] == "unknown", "A kite spot needs the wind"

        # A missing period must not crash and must not beat a known good one.
        no_period = rate_spot(spot, {"swell_height_m": 1.4, "wind_speed_kt": 16.0,
                                     "swell_direction_deg": spot["shore_normal_deg"],
                                     "wind_direction_deg": spot["shore_normal_deg"] + 90})
        assert 0.0 <= no_period["score"] <= 10.0
    assert rating_from_score(0.0) != "unknown", \
        "'unknown' must never come from a score, only from missing data"
    print("  empty / null / partial conditions degrade gracefully: OK")


def test_rating_bands_and_labels_are_complete() -> None:
    for score, want in ((0.0, "flat"), (0.4, "flat"), (1.5, "poor"),
                        (4.0, "fair"), (7.0, "good"), (9.5, "epic"), (10.0, "epic")):
        got = rating_from_score(score)
        assert got == want, f"score {score} -> {got}, expected {want}"
    for lang in ("es", "en"):
        for rating in RATING_ORDER:
            assert RATING_LABELS[lang].get(rating), f"{lang}: no label for {rating}"
    # Both languages must cover exactly the same reason codes.
    assert set(REASON_LABELS["es"]) == set(REASON_LABELS["en"]), \
        "Reason codes drifted between languages"
    assert all(REASON_LABELS[l][k] for l in ("es", "en") for k in REASON_LABELS[l]), \
        "Empty reason label"
    print("  rating bands + es/en label coverage: OK")


def test_face_height_is_a_sane_range() -> None:
    lo, hi = face_height_ft(1.0, ENCUENTRO)
    assert lo < hi, "Range must be ordered"
    # 1 m swell on a shoaling reef: roughly waist-to-head high, not double.
    assert 2.0 < lo < 4.0 and 3.0 < hi < 6.0, f"1 m -> {lo}-{hi} ft looks wrong"
    assert face_height_ft(0.0, ENCUENTRO) == (0.0, 0.0)
    print(f"  face height 1 m -> {lo}-{hi} ft: OK")


# ---------------------------------------------------------------------------
# Time-series reducers
# ---------------------------------------------------------------------------
def _synthetic_series(hours: int = 72) -> list[dict]:
    """A 3-day hourly series whose best window is midday on day 2."""
    start = dt.datetime(2026, 1, 10, 0, 0, tzinfo=dt.timezone.utc)
    series = []
    for h in range(hours):
        t = start + dt.timedelta(hours=h)
        peak = 1.8 if (t.day == 11 and 14 <= t.hour <= 18) else 0.9
        series.append({
            "time": t,
            "swell_height_m": peak,
            "swell_period_s": 11.0 if peak > 1.0 else 7.0,
            "swell_direction_deg": 20.0,
            "wind_speed_kt": 6.0,
            "wind_direction_deg": 200.0,
            "sample_km": 3.0,
        })
    return series


def test_series_reducers() -> None:
    series = _synthetic_series()
    assert conditions_at([]) is None, "Empty series must return None"

    at = conditions_at(series, dt.datetime(2026, 1, 11, 15, 20, tzinfo=dt.timezone.utc))
    assert at["time"].hour == 15 and at["time"].day == 11, f"Picked {at['time']}"

    # Naive datetimes are treated as UTC rather than raising.
    naive = conditions_at(series, dt.datetime(2026, 1, 11, 15, 0))
    assert naive["time"].hour == 15

    day2 = daylight_window(series, dt.date(2026, 1, 11))
    assert day2, "Day 2 must have daylight hours"
    assert all(10 <= c["time"].hour <= 22 for c in day2), "Window must clip to daylight"
    assert all(c["time"].date() == dt.date(2026, 1, 11) for c in day2)

    outlook = daily_outlook(ENCUENTRO, series, days=3)
    assert len(outlook) == 3, f"Expected 3 days, got {len(outlook)}"
    assert all("day" in d and "at" in d and "conditions" in d for d in outlook)
    # Day 2 carries the peak, so it must out-score the flat days either side.
    assert outlook[1]["score"] > outlook[0]["score"], "Day 2 peak should win"
    assert outlook[1]["at"].hour in range(14, 19), f"Peak at {outlook[1]['at']}"
    assert daily_outlook(ENCUENTRO, [], days=3) == []
    print(f"  reducers: day2 peak {outlook[1]['score']}/10 at "
          f"{outlook[1]['at'].hour:02d}:00 UTC: OK")


# ---------------------------------------------------------------------------
# Live API check (opt-in)
# ---------------------------------------------------------------------------
def test_live_fetch() -> None:
    """Hit the real Open-Meteo endpoints. Only runs with --live."""
    series = fetch_surf_conditions(spot_points(curated_only=True), forecast_days=2)
    assert series, "No spots returned — check the marine endpoint"
    missing = [s["beach"] for s in SURF_SPOTS if s["beach"] not in series]
    assert not missing, f"No data for: {missing}"

    for spot in SURF_SPOTS:
        entries = series[spot["beach"]]
        assert len(entries) >= 24, f"{spot['beach']}: only {len(entries)} hours"
        now = conditions_at(entries)
        assert now["swell_height_m"] is not None, f"{spot['beach']}: null swell"
        assert now["wind_speed_kt"] is not None, f"{spot['beach']}: null wind"
        rated = rate_spot(spot, now)
        assert rated["rating"] in RATING_ORDER
        sport = "🪁" if spot["sport"] == SPORT_WIND else "🏄"
        print(f"  {sport} {spot['beach']:<24} {rated['score']:>4}/10 "
              f"{rated['rating']:<5} {now['swell_height_m']:.1f}m@"
              f"{now['swell_period_s'] or 0:.0f}s from "
              f"{compass_point(now['swell_direction_deg'] or 0):<3} · wind "
              f"{now['wind_speed_kt']:.0f}kt {rated['wind_relation']:<14} "
              f"(grid {now['sample_km']}km off)")


OFFLINE_TESTS = (
    test_dataset_joins_to_beaches,
    test_dataset_fields_are_coherent,
    test_lookup_helpers,
    test_every_beach_gets_a_rating,
    test_angular_diff_wraps,
    test_compass_point,
    test_wind_relation_for_north_facing_beach,
    test_size_score_window,
    test_period_score_is_monotonic_and_bounded,
    test_swell_direction_score_tapers_off_axis,
    test_wind_score_surf_prefers_offshore,
    test_wind_strength_and_direction_for_kiting,
    test_classic_winter_groundswell_rates_high,
    test_flat_summer_day_rates_flat,
    test_onshore_windswell_beats_nothing_but_is_not_good,
    test_oversize_day_is_poor_not_flat,
    test_off_axis_swell_is_shadowed,
    test_kite_spot_scenarios,
    test_surf_swell_does_not_inflate_a_kite_spot,
    test_every_spot_rates_across_a_condition_sweep,
    test_missing_fields_degrade_gracefully,
    test_rating_bands_and_labels_are_complete,
    test_face_height_is_a_sane_range,
    test_series_reducers,
)


def main(argv: list[str]) -> int:
    for fn in OFFLINE_TESTS:
        print(fn.__name__)
        fn()
    print(f"\nAll {len(OFFLINE_TESTS)} surf test groups passed (offline).")

    if "--live" in argv:
        print("\ntest_live_fetch (hitting Open-Meteo)")
        test_live_fetch()
        print("\nLive fetch OK.")
    else:
        print("Run with --live to also check the Open-Meteo endpoints.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
