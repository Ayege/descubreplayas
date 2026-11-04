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

"""Offline tests for the "Conoce esta playa" data files and logic layer.

Run from the repo root:

    python -m tests.test_profiles
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path
from unittest import mock

from dashboard import conoce_data as cd
from dashboard.beach_profiles import COASTAL_UNITS, MARINE_OVERRIDES, PROFILES
from dashboard.beaches_data import BEACHES
from tests.conoce_fixtures import (
    BEACH, SOURCE, UNIT, auto, historia, loc, note, photo, profile, roca, sourced, taxon, teaser, unit, vida,
)

_REPO = Path(__file__).resolve().parent.parent
_MAX_PROFILES_BYTES = 200 * 1024
_PLACEHOLDER = "[PLACEHOLDER]"
_UNITS = {UNIT: unit()}


def _check(condition: bool, message: str) -> None:
    assert condition, message


def _strings(value: object, path: str = "$") -> list[tuple[str, str]]:
    """Every string in a nested value, including dict keys, with its path."""
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        found = [hit for key, child in value.items() for hit in _strings(child, f"{path}.{key}")]
        return found + [(f"{path}.<key>", key) for key in value if isinstance(key, str)]
    if isinstance(value, (list, tuple)):
        return [hit for i, child in enumerate(value) for hit in _strings(child, f"{path}[{i}]")]
    return []


def _placeholder_paths(value: object) -> list[str]:
    return [path for path, text in _strings(value) if _PLACEHOLDER.casefold() in text.casefold()]


def _shipped_auto() -> dict:
    # Parsed directly, not through read_auto_species: that reader turns a corrupt
    # file into empty data by design, which here would pass without checking.
    return json.loads(cd.AUTO_SPECIES_PATH.read_text(encoding="utf-8"))


def test_every_beach_has_a_valid_profile() -> None:
    names = {b["name"] for b in BEACHES}
    _check(set(PROFILES) == names, "PROFILES must have exactly the beaches in BEACHES")
    for beach, raw in PROFILES.items():
        _check(cd.profile_problems(raw) == [], f"{beach}: {cd.profile_problems(raw)}")
    for key, raw in COASTAL_UNITS.items():
        _check(cd.unit_problems(raw) == [], f"{key}: {cd.unit_problems(raw)}")
    used = {raw["coastal_unit"] for raw in PROFILES.values()}
    _check(used == set(COASTAL_UNITS), "every unit should be used by a beach")


def test_shipped_species_file() -> None:
    payload = _shipped_auto()
    names = {b["name"] for b in BEACHES}
    _check(payload["schema_version"] == cd.AUTO_SCHEMA_VERSION, "schema version")
    for key, record in payload["taxa"].items():
        _check(cd.taxon_problems(record) == [], f"{key}: {cd.taxon_problems(record)}")
        if record["photo"] is not None:
            _check(cd.photo_problems(record["photo"]) == [], f"{key}: {cd.photo_problems(record['photo'])}")
    for beach, references in payload["beaches"].items():
        _check(beach in names, f"unknown beach in species_auto: {beach}")
        for reference in references:
            _check(cd.beach_taxon_problems(reference) == [], f"{beach}: bad reference")
            _check(str(reference["taxon_id"]) in payload["taxa"], f"{beach}: reference to a missing taxon")


def test_species_file_has_no_orphans() -> None:
    problems = cd.auto_reference_problems(_shipped_auto())
    _check(problems == [], f"species_auto.json: {problems}")


def test_orphan_detector() -> None:
    good = auto(taxon(1))
    _check(cd.auto_reference_problems(good) == [], "a clean file has no problems")
    dangling = auto(taxon(1))
    dangling["beaches"][BEACH].append({"taxon_id": 2, "observation_count": 1})
    _check(any("dangling" in p for p in cd.auto_reference_problems(dangling)), "must catch a dangling reference")
    unused = auto(taxon(1))
    unused["taxa"]["3"] = taxon(3)
    _check(any("not referenced" in p for p in cd.auto_reference_problems(unused)), "must catch an unreferenced species")
    for bad in ({}, {"taxa": [], "beaches": {}}, {"taxa": {}, "beaches": 5}):
        _check(cd.auto_reference_problems(bad) != [], f"must reject: {bad}")
    _check(cd.auto_reference_problems({"taxa": {}, "beaches": {BEACH: ["x", 5]}}) == [], "garbage entries do not raise")


def test_file_budgets() -> None:
    _check(cd.AUTO_SPECIES_PATH.stat().st_size <= cd.MAX_AUTO_SPECIES_BYTES, "species_auto.json over budget")
    _check((_REPO / "dashboard" / "beach_profiles.py").stat().st_size <= _MAX_PROFILES_BYTES, "beach_profiles.py over budget")
    _check(cd.AUTO_SPECIES_PATH == _REPO / "dashboard" / "data" / "species_auto.json", "spec path")


def test_shipped_text_is_plain() -> None:
    for path, text in _strings([PROFILES, COASTAL_UNITS, _shipped_auto()]):
        _check(cd.FORBIDDEN_TEXT_RE.search(text) is None, f"markup or control character at {path}")


def test_no_placeholder_in_shipped_data() -> None:
    hits = _placeholder_paths([PROFILES, COASTAL_UNITS, _shipped_auto()])
    _check(not hits, f"{_PLACEHOLDER} found in shipped data: {hits}")


def test_placeholder_detector_finds_nested_values() -> None:
    item = historia()
    item["body"]["en"] = f"see {_PLACEHOLDER} here"
    _check(_placeholder_paths({BEACH: profile(historia=[item])}) != [], "missed a nested value")
    _check(_placeholder_paths({_PLACEHOLDER.lower(): 1}) != [], "missed a key, or a case change")
    _check(_placeholder_paths({BEACH: profile(historia=[historia()])}) == [], "false positive on clean data")


def test_budget_constants_match_the_spec() -> None:
    # Literal on purpose: these numbers are the spec, not a copy of the constants.
    actual = (cd.MAX_TEASER_CHARS, cd.MAX_BLURB_CHARS, cd.MAX_GEOLOGY_CHARS, cd.MAX_HISTORY_BODY_CHARS, cd.MAX_HISTORY_ITEMS)
    _check(actual == (90, 140, 400, 500, 3), "text budgets")
    _check((cd.MAX_FEATURED_SPECIES, cd.MAX_MORE_SPECIES, cd.MAX_SPECIES_TOTAL) == (3, 5, 8), "3 visible + 5 more")


def test_methods() -> None:
    _check(cd.ALLOWED_METHODS == {"curated", "llm_draft_reviewed", "api:inaturalist", "api:macrostrat"}, "allowed methods")
    bad = taxon()
    for method in ("api:gbif", "api:obis", "curated"):
        bad["method"] = method
        _check(cd.taxon_problems(bad) != [], f"{method} must not generate a species")
    reviewed = sourced(method=cd.METHOD_LLM_REVIEWED, verified_at="2026-11-04", title=loc(), body=loc(), year=None)
    _check(cd.history_problems(reviewed) == [], "reviewed draft with verified_at should pass")
    reviewed.pop("verified_at")
    _check(cd.history_problems(reviewed) != [], "llm draft needs verified_at")


def _set_source(url: str):
    return lambda r: r["sources"][0].__setitem__("url", url)


_BAD_HISTORIA = {
    "no sources": lambda r: r.__setitem__("sources", []),
    "sources missing": lambda r: r.pop("sources"),
    "too many sources": lambda r: r.__setitem__("sources", [dict(SOURCE)] * (cd.MAX_SOURCES_PER_FACT + 1)),
    "unknown method": lambda r: r.__setitem__("method", "guess"),
    "unhashable method": lambda r: r.__setitem__("method", ["curated"]),
    "api method": lambda r: r.__setitem__("method", "api:inaturalist"),
    "bad verified_at": lambda r: r.__setitem__("verified_at", "soon"),
    "missing english": lambda r: r["body"].pop("en"),
    "blank spanish": lambda r: r["body"].__setitem__("es", "  "),
    "body over budget": lambda r: r["body"].__setitem__("es", "x" * (cd.MAX_HISTORY_BODY_CHARS + 1)),
    "markup": lambda r: r["body"].__setitem__("es", "a <b>b</b>"),
    "control character": lambda r: r["body"].__setitem__("en", "a\x07b"),
    "bidi override": lambda r: r["body"].__setitem__("en", "a‮b"),
    "bool year": lambda r: r.__setitem__("year", True),
    "text year": lambda r: r.__setitem__("year", "1900"),
    "extra key": lambda r: r.__setitem__("latitude", 18.5),
    "source http": _set_source("http://example.org/"),
    "source javascript": _set_source("javascript:alert(1)"),
    "source data": _set_source("data:text/html,x"),
    "source username": _set_source("https://u@example.org/"),
    "source password": _set_source("https://:p@example.org/"),
    "source backslash": _set_source("https://evil.test\\@example.org/"),
    "source newline": _set_source("https://example.org/\nx"),
    "source quote": _set_source('https://example.org/"x'),
    "source no host": _set_source("https:///path"),
    "source bad port": _set_source("https://example.org:bad/"),
}


def test_bad_historia_rejected() -> None:
    for label, mutate in _BAD_HISTORIA.items():
        record = historia()
        mutate(record)
        _check(cd.history_problems(record) != [], f"should be rejected: {label}")


def _set_photo(key: str, value: object):
    return lambda r: r.__setitem__(key, value)


_HOST = "https://inaturalist-open-data.s3.amazonaws.com"
_BAD_PHOTOS = {
    "license nc": _set_photo("license_code", "cc-by-nc"),
    "license nd": _set_photo("license_code", "cc-by-nd"),
    "license nc-sa": _set_photo("license_code", "cc-by-nc-sa"),
    "license none": _set_photo("license_code", None),
    "license unhashable": _set_photo("license_code", ["cc0"]),
    "no attribution": _set_photo("attribution", ""),
    "closed host": _set_photo("url", "https://static.inaturalist.org/photos/1/square.jpg"),
    "other host": _set_photo("url", "https://evil.test/photos/1/square.jpg"),
    "lookalike host": _set_photo("url", f"{_HOST}.evil.test/photos/1/square.jpg"),
    "too large": _set_photo("url", f"{_HOST}/photos/1/original.jpg"),
    "other port": _set_photo("url", f"{_HOST}:8443/photos/1/square.jpg"),
    "unsafe source": _set_photo("source_url", "javascript:alert(1)"),
    "extra key": _set_photo("latitude", 18.5),
}


def test_bad_photo_keeps_the_species() -> None:
    _check(cd.photo_problems(photo()) == [], "the valid photo should pass")
    for label, mutate in _BAD_PHOTOS.items():
        record = taxon()
        mutate(record["photo"])
        _check(cd.photo_problems(record["photo"]) != [], f"should be rejected: {label}")
        species = cd.build_index({}, {}, auto(record))[BEACH]["species"]
        _check(len(species) == 1, f"species should stay: {label}")
        _check(species[0]["photo"] is None, f"photo should go: {label}")


def test_taxon_shape() -> None:
    _check(cd.taxon_problems(taxon()) == [], "the valid taxon should pass")
    for key in ("latitude", "longitude", "location", "geojson"):
        record = taxon()
        record[key] = 1
        _check(cd.taxon_problems(record) != [], f"coordinates must not fit the shape: {key}")
    record = taxon()
    record["common_name"]["es"] = None
    _check(cd.taxon_problems(record) == [], "a missing common name is allowed")
    species = cd.build_index({}, {}, auto(record))[BEACH]["species"][0]
    _check(species["common_name"]["es"] == record["scientific_name"], "fall back to the scientific name")
    _check(species["common_name"]["en"] == "Species 1", "the other language keeps its name")


def test_profile_rules() -> None:
    _check(cd.profile_problems(profile(teaser=teaser(), historia=[historia()]), _UNITS) == [], "valid profile")
    both = profile(vida=vida(pinned=[1], hidden=[1]))
    _check(cd.profile_problems(both, _UNITS) != [], "a taxon cannot be pinned and hidden")
    _check(cd.profile_problems(profile(coastal_unit="nope"), _UNITS) != [], "unknown coastal unit")
    _check(cd.profile_problems(profile(vida=vida(pinned=["1"])), _UNITS) != [], "pinned ids are integers")
    _check(cd.profile_problems(profile(vida=vida(notes={"1": note()})), _UNITS) != [], "note keys are integers")
    _check(cd.profile_problems(profile(historia=[historia()] * 4), _UNITS) != [], "too many history items")
    _check(cd.profile_problems(profile(radius_km=0), _UNITS) != [], "radius must be positive")
    _check(cd.profile_problems(profile(radius_km=5), _UNITS) == [], "a beach may widen its radius")
    plain_teaser = teaser()
    plain_teaser["text"]["en"] = "x" * (cd.MAX_TEASER_CHARS + 1)
    _check(cd.teaser_problems(plain_teaser) != [], "teaser over budget")
    unsourced_note = note()
    unsourced_note.pop("sources")
    _check(cd.note_problems(unsourced_note) != [], "a species note needs sources")


def test_unit_rules() -> None:
    _check(cd.unit_problems(unit(with_roca=False)) == [], "an empty stub is valid")
    _check(cd.unit_problems(unit(why=True)) == [], "a sourced unit may explain sargassum")
    _check(cd.unit_problems(unit(with_roca=False, why=True)) != [], "the sargassum line needs a sourced roca block")
    bad = roca()
    bad["sources"] = []
    _check(cd.roca_problems(bad) != [], "roca needs a source")
    bad = roca()
    bad["story"]["es"] = "x" * (cd.MAX_GEOLOGY_CHARS + 1)
    _check(cd.roca_problems(bad) != [], "story over budget")


def test_garbage_never_raises() -> None:
    garbage = [None, 7, "x", [], {}, [None], {"es": 1, "en": 2}, {1: 2, "a": 3}, [[]]]
    for value in garbage:
        cd.build_index({BEACH: value}, {UNIT: value}, {"taxa": value, "beaches": value})
        cd.build_index({BEACH: profile(vida=value)}, {}, {"taxa": {"1": value}, "beaches": {BEACH: value}})
        cd.build_index({BEACH: profile(historia=value, teaser=value)}, {UNIT: value}, auto(taxon()))
        cd.profile_problems(value)
        cd.profile_problems(profile(vida=value, historia=value, teaser=value, coastal_unit=value))
        cd.unit_problems(value)
        record = taxon()
        for key in tuple(record):
            record[key] = value
        cd.taxon_problems(record)
        cd.photo_problems(value)
        _check(cd.history_problems(value) != [], "garbage history passed")


def _ranked_ids(*args, **kwargs) -> list[int]:
    return [t["taxon_id"] for t in cd.rank_taxa(*args, **kwargs)]


def _rankable(taxon_id: int, count: int, marine: bool = False, has_photo: bool = True, group: str = "Aves") -> dict:
    return {
        "taxon_id": taxon_id,
        "scientific_name": f"Testus n{taxon_id}",
        "iconic_group": group,
        "observation_count": count,
        "marine": marine,
        "photo": photo() if has_photo else None,
    }


def test_ranking_tiers_and_tiebreak() -> None:
    taxa = [
        _rankable(1, 100, marine=True, has_photo=False, group="A"),
        _rankable(2, 90, has_photo=False, group="B"),
        _rankable(3, 5, group="C"),
        _rankable(4, 9, group="D"),
        _rankable(5, 2, marine=True, group="E"),
        _rankable(6, 3, marine=True, group="F"),
    ]
    _check(_ranked_ids(taxa) == [6, 5, 4, 3, 1, 2], "tiers, then observation count inside each tier")
    tied = [_rankable(8, 5, group="A"), _rankable(7, 5, group="B")]
    _check(_ranked_ids(tied) == [7, 8], "ties must have a stable order")


def test_ranking_diversity_rule() -> None:
    birds = [_rankable(i, 100 - i, marine=True, group="Aves") for i in range(1, 5)]
    fish = _rankable(9, 1, group="Pez")
    ranked = _ranked_ids([*birds, fish])
    _check(ranked[:3] == [1, 2, 9], "at most 2 of one group among the top 3")
    _check(ranked[3:] == [3, 4], "the moved taxa keep their order below")
    _check(_ranked_ids(birds)[:3] == [1, 2, 3], "one group only: fill the slots anyway")


def test_ranking_allows_one_plant_in_the_top() -> None:
    _check(cd.MAX_PLANTS_IN_TOP == 1 and cd.MAX_SAME_GROUP_IN_TOP == 2, "the spec's limits")
    plants = [_rankable(i, 100 - i, marine=True, group="Plantae") for i in range(1, 4)]
    crab = _rankable(8, 2, marine=True, group="Animalia")
    fish = _rankable(9, 1, marine=True, group="Actinopterygii")
    ranked = _ranked_ids([*plants, crab, fish])
    _check(ranked[:3] == [1, 8, 9], "at most 1 plant in the top 3")
    _check(ranked[3:] == [2, 3], "the other plants stay below, in order")
    _check(_ranked_ids([*plants, crab])[:3] == [1, 8, 2], "short of other groups: a second plant fills the slot")
    _check(_ranked_ids(plants) == [1, 2, 3], "plants only: fill the slots anyway")
    pinned = _ranked_ids([*plants, crab, fish], pinned=[3, 2])
    _check(pinned[:3] == [3, 2, 8], "pinned plants stay, and the free slot takes no other plant")


def test_ranking_pinned_and_hidden() -> None:
    plain = _rankable(1, 1, has_photo=False, group="Aves")
    marine = _rankable(2, 50, marine=True, group="Pez")
    other = _rankable(3, 40, marine=True, group="Reptil")
    _check(_ranked_ids([plain, marine, other], pinned=[1]) == [1, 2, 3], "pinned beats every tier")
    _check(_ranked_ids([plain, marine, other], pinned=[3, 1]) == [3, 1, 2], "pinned keeps its order")
    _check(_ranked_ids([plain, marine], hidden=[2]) == [1], "hidden never shows")
    _check(_ranked_ids([plain, marine], pinned=[2], hidden=[2]) == [1], "hidden wins over pinned")
    _check(_ranked_ids([marine], pinned=[99]) == [2], "a pinned taxon with no record is skipped")
    birds = [_rankable(i, 10, group="Aves") for i in range(4, 7)]
    pins = [_rankable(10, 1, group="Aves"), _rankable(11, 1, group="Aves")]
    fish = _rankable(12, 1, group="Pez")
    _check(_ranked_ids([*pins, *birds, fish], pinned=[10, 11])[:3] == [10, 11, 12], "pinned count toward a group")


def test_marine_override_corrects_the_tag() -> None:
    palm = taxon(48865, group="Plantae", marine=True)
    fish = taxon(2, group="Pez", marine=True)
    land = taxon(3, group="Aves", marine=False)
    data = auto(palm, land, fish, counts={48865: 99, 3: 50, 2: 1})
    species = cd.build_index({}, {}, data)[BEACH]["species"]
    _check([s["taxon_id"] for s in species][0] == 2, "the real marine species should rank first")
    _check(next(s for s in species if s["taxon_id"] == 48865)["marine"] is False, "the palm is corrected")
    unchanged = cd.build_index({}, {}, data, overrides={})[BEACH]["species"]
    _check(unchanged[0]["taxon_id"] == 48865, "without the override the OBIS tag stands")
    expected = {48865: "Cocos nucifera", 189657: "Barringtonia asiatica", 278899: "Hernandia sonora", 160172: "Cassytha filiformis", 553515: "Rhinella marina"}
    _check(MARINE_OVERRIDES == dict.fromkeys(expected, False), "these ids were confirmed by name through the API")
    source = (_REPO / "dashboard" / "beach_profiles.py").read_text(encoding="utf-8")
    for name in expected.values():
        _check(f"# {name}: " in source, f"{name} needs its reason beside the override")
    for taxon_id, value in MARINE_OVERRIDES.items():
        _check(isinstance(taxon_id, int) and isinstance(value, bool), "overrides map taxon id to bool")


def test_merge_with_notes_and_sources() -> None:
    data = auto(taxon(1), taxon(2))
    raw = profile(vida=vida(notes={1: note(sargassum=True)}))
    knowledge = cd.build_index({BEACH: raw}, {UNIT: unit()}, data)[BEACH]
    first = next(s for s in knowledge["species"] if s["taxon_id"] == 1)
    _check(first["blurb"]["es"] == "nota es" and first["sargassum_note"]["en"] == "sarg en", "note merged")
    _check(len(first["sources"]) == 2, "the note's sources join the API source")
    second = next(s for s in knowledge["species"] if s["taxon_id"] == 2)
    url = second["sources"][0]["url"]
    _check(second["sources"][0]["label"] == "iNaturalist" and "taxon_id=2" in url and "radius=2" in url, "observation search link")
    _check(cd.is_safe_url(url), "the source link must pass the URL check")
    wide = cd.build_index({BEACH: profile(radius_km=5)}, {UNIT: unit()}, auto(taxon(1)))[BEACH]
    _check("radius=5" in wide["species"][0]["sources"][0]["url"], "a beach's radius reaches its link")
    ghost = cd.build_index({}, {}, auto(taxon(1), beach="Playa inexistente"))
    _check(ghost["Playa inexistente"]["species"] == [], "no coordinates, no source, no species")


def test_species_cap() -> None:
    taxa = [taxon(i) for i in range(1, 21)]
    _check(len(cd.build_index({}, {}, auto(*taxa))[BEACH]["species"]) == cd.MAX_SPECIES_TOTAL, "8 species at most")


def test_bad_record_only_drops_itself() -> None:
    bad = historia("mala")
    bad["sources"] = []
    raw = profile(historia=[bad, historia("buena")], teaser=sourced(text=loc("x" * 200, "y")))
    knowledge = cd.build_index({BEACH: raw, "Otra": 5}, {UNIT: unit()}, auto(taxon(1)))[BEACH]
    _check([h["title"]["es"] for h in knowledge["historia"]] == ["buena es"], "only the bad item should go")
    _check(knowledge["teaser"] is None, "the bad teaser goes alone")
    _check(knowledge["roca"] is not None and len(knowledge["species"]) == 1, "other lenses should stay")


def test_public_api_and_cache() -> None:
    fake = cd.build_index({BEACH: profile(historia=[historia()])}, {UNIT: unit()}, auto(taxon(1), taxon(2)))
    with mock.patch.object(cd, "load_index", return_value=fake):
        _check(cd.get_profile(BEACH)["coastal_unit"] == UNIT, "get_profile")
        _check(cd.coastal_unit_for(BEACH)["name"]["es"] == "Unidad", "coastal_unit_for")
        _check(len(cd.featured_species(BEACH, limit=1)) == 1 and len(cd.featured_species(BEACH)) == 2, "featured_species")
        _check(cd.has_content(BEACH) == {"vida": True, "roca": True, "historia": True}, "has_content")
        _check(cd.get_profile("x") is None and cd.featured_species("x") == [] and cd.coastal_unit_for("x") is None, "unknown")
        _check(cd.has_content("x") == {"vida": False, "roca": False, "historia": False}, "unknown has_content")
    cd.load_index.cache_clear()
    with mock.patch.object(cd, "read_auto_species", wraps=cd.read_auto_species) as reader:
        for _ in range(1000):
            cd.get_profile(BEACH)
    _check(reader.call_count == 1, f"1,000 lookups read the file {reader.call_count} times")
    cd.load_index.cache_clear()


def test_read_auto_species_failures() -> None:
    empty = {"taxa": {}, "beaches": {}}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "auto.json"
        _check(cd.read_auto_species(path) == empty, "a missing file should read as empty")
        for body in ("{not json", "[]", '{"schema_version": 99, "taxa": {}, "beaches": {}}',
                     '{"schema_version": 1, "taxa": [], "beaches": {}}', '{"schema_version": 1}'):
            path.write_text(body, encoding="utf-8")
            _check(cd.read_auto_species(path) == empty, f"should read as empty: {body}")


def test_logic_layer_has_no_streamlit_or_network() -> None:
    source = (_REPO / "dashboard" / "conoce_data.py").read_text(encoding="utf-8")
    for forbidden in ("import streamlit", "import httpx", "import requests", "import socket", "urllib.request"):
        _check(forbidden not in source, f"the logic layer must not use {forbidden}")


def main() -> int:
    logging.disable(logging.CRITICAL)
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    for name, test in tests:
        test()
        print(f"  ok {name}")
    print(f"All {len(tests)} profile tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
