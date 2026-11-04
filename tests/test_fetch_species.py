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

"""Offline tests for scripts/fetch_species.py. Nothing here touches the network.

The fixtures in tests/fixtures/ are real API responses, trimmed. Run from the
repo root:

    python -m tests.test_fetch_species
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

import httpx

from dashboard import conoce_data as cd
from dashboard.beaches_data import BEACHES
from scripts import fetch_species as fs

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_BEACH = next(b for b in BEACHES if b["name"] == "Playa Bávaro")
_TODAY = "2026-11-04"
_DR = 6931
_DR_PARENT = 97394
_IMAGE = "https://inaturalist-open-data.s3.amazonaws.com/photos/{}/square.jpg"
_PELICAN, _PALM, _ARDEA = 4328, 48865, 411086


def _check(condition: bool, message: str) -> None:
    assert condition, message


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _json(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


class _Server:
    """A fake of the three endpoints, serving the recorded responses by default."""

    def __init__(self, counts: dict | None = None, taxa: dict | None = None, obis: dict | None = None) -> None:
        self.counts = counts or _load("inat_species_counts.json")
        self.taxa = taxa or _load("inat_taxa.json")
        self.obis = obis or _load("obis_checklist.json")
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/species_counts"):
            return _json(self.counts)
        if "/taxa/" in path:
            ids = {int(i) for i in unquote(path.rsplit("/", 1)[1]).split(",")}
            return _json({"results": [t for t in self.taxa["results"] if t["id"] in ids]})
        return _json(self.obis)

    def paths(self, marker: str) -> list[httpx.Request]:
        return [r for r in self.requests if marker in r.url.path]


def _api(handler) -> fs.Api:
    return fs.Api(fs.make_client(httpx.MockTransport(handler)), sleep=lambda _: None)


def _profile(**vida) -> dict:
    return {"vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}, **vida}}


def _run(server: _Server, profile: dict | None = None) -> fs.BeachResult:
    return fs.run_beach(_api(server), _BEACH, profile or _profile(), fs.Caches(), _TODAY)


def _allowed_photo(photo_id: int = 7) -> dict:
    return {"id": photo_id, "license_code": "cc-by", "attribution": "(c) A, some rights reserved (CC BY)",
            "attribution_name": "Autora", "square_url": _IMAGE.format(photo_id)}


def _taxon_row(taxon_id: int = 1, name: str = "Testus uno", default: dict | None = None, count: int = 3, **extra) -> dict:
    return {"count": count, "taxon": {
        "id": taxon_id, "name": name, "preferred_common_name": "uno", "english_common_name": "one",
        "iconic_taxon_name": "Aves", "default_photo": default, **extra,
    }}


def _detail(taxon_id: int = 1, name: str = "Testus uno", **fields) -> dict:
    return {"id": taxon_id, "name": name, "conservation_statuses": [], "taxon_photos": [], **fields}


def _one_taxon_server(row: dict | None = None, detail: dict | None = None) -> _Server:
    return _Server(
        counts={"total_results": 1, "results": [row or _taxon_row()]},
        taxa={"results": [detail or _detail()]},
        obis={"total": 0, "results": []},
    )


def test_real_response_end_to_end() -> None:
    server = _Server()
    result = _run(server)
    kept = set(result.taxa)
    _check(_PELICAN in kept, "the pelican should be kept")
    names = {t["scientific_name"] for t in result.taxa.values()}
    _check("Psittacara chloropterus" not in names, "a globally obscured taxon must go")
    _check(result.excluded_by_status == 1 and result.excluded_lookup_failed == 0, "one taxon excluded by status")
    pelican = result.taxa[_PELICAN]
    _check(cd.taxon_problems(pelican) == [], f"{cd.taxon_problems(pelican)}")
    _check(pelican["photo"]["origin"] == "taxon_photos", "the pelican's default photo is not allowed")
    _check(pelican["common_name"]["en"] == "Brown Pelican", "english_common_name supplies the English")
    _check(cd.photo_problems(pelican["photo"]) == [], f"{cd.photo_problems(pelican['photo'])}")


def test_marine_provenance() -> None:
    obis = _load("obis_checklist.json")
    obis["results"].append({"scientificName": "Cocos nucifera", "is_marine": True, "is_brackish": True, "is_terrestrial": True})
    result = _run(_Server(obis=obis))
    pelican = result.taxa[_PELICAN]
    _check(pelican["marine"] is True, "OBIS tags the pelican marine")
    _check(pelican["marine_source"] == "obis" and pelican["marine_tagged_at"] == _TODAY, "the tag keeps its provenance")


def test_override_applies_to_ingest_ranking() -> None:
    palm = _taxon_row(_PALM, "Cocos nucifera", default=_allowed_photo(1), count=99)
    fish = _taxon_row(2, "Testus pez", default=_allowed_photo(2), count=1)
    server = _Server(
        counts={"total_results": 2, "results": [palm, fish]},
        taxa={"results": [_detail(_PALM, "Cocos nucifera"), _detail(2, "Testus pez")]},
        obis={"total": 2, "results": [
            {"scientificName": "Cocos nucifera", "is_marine": True, "is_brackish": True, "is_terrestrial": True},
            {"scientificName": "Testus pez", "is_marine": True},
        ]},
    )
    result = _run(server)
    _check(result.references[0]["taxon_id"] == 2, "the palm must not rank as marine, with or without photos")
    _check(result.marine == 1, "the summary counts the corrected tag")
    _check(result.taxa[_PALM]["marine"] is True and result.taxa[_PALM]["marine_source"] == "obis", "the file keeps OBIS's answer")


def test_marine_flags() -> None:
    _check(fs._is_marine_record({"is_marine": True}), "marine")
    _check(fs._is_marine_record({"is_brackish": True}), "brackish counts")
    for record in ({"is_terrestrial": True}, {"is_freshwater": True}, {"is_marine": None}, {}):
        _check(not fs._is_marine_record(record), f"not marine: {record}")


def test_obis_no_match_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, json={"total": 0, "results": [], "error": "NAME_NOT_FOUND,NAME_NOT_FOUND"})

    cache: dict = {}
    fs.fetch_marine_tags(_api(handler), ["Anolis uno", "Anolis dos"], cache)
    _check(len(calls) == 1, "HTTP 500 NAME_NOT_FOUND must not be retried")
    _check(cache == {"Anolis uno": fs.MarineTag(False), "Anolis dos": fs.MarineTag(False)}, "no match means not marine")
    _check("lat" not in calls[0].url.params and "geometry" not in calls[0].url.params, "no coordinates go to OBIS")


def test_failed_obis_batch_means_unknown() -> None:
    server = _one_taxon_server(_taxon_row(default=_allowed_photo()))

    def handler(request: httpx.Request) -> httpx.Response:
        if "checklist" in request.url.path:
            return httpx.Response(503, json={})
        return server(request)

    record = fs.run_beach(_api(handler), _BEACH, _profile(), fs.Caches(), _TODAY).taxa[1]
    _check(record["marine"] is False and record["marine_source"] is None and record["marine_tagged_at"] is None,
           "a failed lookup is recorded as unknown, not as an OBIS answer")


def test_obis_truncation_warns() -> None:
    with mock.patch.object(fs.logger, "warning") as warning:
        fs.fetch_marine_tags(_api(lambda _: _json({"total": 11, "results": []})), ["A b"], {})
    _check(warning.called, "a truncated OBIS page must be logged")


def _photo_case(fields: dict) -> fs.BeachResult:
    return _run(_one_taxon_server(detail=_detail(**fields)))


def test_taxon_photos_defensive_cases() -> None:
    bad_license = dict(_allowed_photo(), license_code="cc-by-nc")
    bad_host = dict(_allowed_photo(), square_url="https://static.inaturalist.org/photos/7/square.jpg")
    other_host = dict(_allowed_photo(), square_url="https://evil.test/photos/7/square.jpg")
    no_license = dict(_allowed_photo(), license_code=None)
    cases = {
        "missing field": {"taxon_photos": None},
        "empty list": {"taxon_photos": []},
        "not a list": {"taxon_photos": {"photo": _allowed_photo()}},
        "malformed entry": {"taxon_photos": ["x", 5, None, {"nope": 1}, {"photo": "x"}, {"photo": {"id": "x"}}]},
        "disallowed license": {"taxon_photos": [{"photo": bad_license}]},
        "all rights reserved": {"taxon_photos": [{"photo": no_license}]},
        "closed host": {"taxon_photos": [{"photo": bad_host}]},
        "other host": {"taxon_photos": [{"photo": other_host}]},
    }
    for label, fields in cases.items():
        if label == "missing field":
            detail = _detail()
            del detail["taxon_photos"]
            result = _run(_one_taxon_server(detail=detail))
        else:
            result = _photo_case(fields)
        _check(1 in result.taxa, f"the species must stay: {label}")
        _check(result.taxa[1]["photo"] is None, f"the photo must go: {label}")
        _check(result.photos_taxon == 0, f"no photo counted: {label}")


def test_taxon_photo_uses_its_own_attribution() -> None:
    good = _allowed_photo(9)
    result = _photo_case({"taxon_photos": [{"photo": dict(_allowed_photo(), license_code="cc-by-nc")}, {"photo": good}]})
    photo = result.taxa[1]["photo"]
    _check(photo["attribution"] == "Autora", "the attribution comes from the photo object")
    _check(photo["license_code"] == "cc-by" and photo["url"] == good["square_url"], "license and URL from the same photo")
    _check(photo["source_url"].endswith("/photos/9"), "the credit links to that photo")
    _check(photo["origin"] == "taxon_photos", "the origin is recorded")
    _check(result.photos_taxon == 1 and result.photos_default == 0, "counted as a taxon photo")
    for word in ("playa", "beach", "bávaro", "observation"):
        _check(word not in photo["attribution"].lower(), f"attribution must not place the photo: {word}")


def test_default_photo_wins_when_allowed() -> None:
    row = _taxon_row(default=_allowed_photo(5))
    result = _run(_one_taxon_server(row, _detail(taxon_photos=[{"photo": _allowed_photo(6)}])))
    _check(result.taxa[1]["photo"]["url"] == _IMAGE.format(5), "the default photo comes first")
    _check(result.photos_default == 1 and result.photos_taxon == 0, "counted as a default photo")


def _status(geoprivacy: str | None, place_id: int | None) -> dict:
    place = {"id": place_id, "name": "x"} if place_id is not None else None
    return {"status": "x", "geoprivacy": geoprivacy, "place": place}


def test_privacy_rule() -> None:
    by_status = {
        "global obscured": [_status("obscured", None)],
        "global private": [_status("private", None)],
        "DR obscured": [_status("obscured", _DR)],
        "mixed": [_status("open", None), _status("obscured", _DR)],
        "obscured on a place containing the DR": [_status("obscured", _DR_PARENT)],
        "place with no id": [{"geoprivacy": "obscured", "place": {}}],
        "place not an object": [{"geoprivacy": "private", "place": "x"}],
        "place id not a number": [{"geoprivacy": "obscured", "place": {"id": "6931"}}],
        "text place_id": [{"geoprivacy": "obscured", "place_id": "x"}],
    }
    for label, statuses in by_status.items():
        _check(fs.privacy_exclusion({}, {"conservation_statuses": statuses}) == fs.EXCLUDED_BY_STATUS, f"by status: {label}")
    kept = {
        "none": [],
        "elsewhere": [_status("obscured", 12345)],
        "open": [_status("open", None), _status("open", _DR)],
        "threatened but not obscured": [{"status": "CR", "geoprivacy": None, "place": None}],
    }
    for label, statuses in kept.items():
        _check(fs.privacy_exclusion({}, {"conservation_statuses": statuses}) is None, f"should stay: {label}")
    for label, detail in (("no detail", None), ("no field", {}), ("malformed", {"conservation_statuses": "x"}), ("bad entry", {"conservation_statuses": ["x"]})):
        _check(fs.privacy_exclusion({}, detail) == fs.EXCLUDED_LOOKUP_FAILED, f"fail closed: {label}")
    summary = {"conservation_status": {"geoprivacy": "obscured", "place_id": None}}
    _check(fs.privacy_exclusion(summary, {"conservation_statuses": []}) == fs.EXCLUDED_BY_STATUS, "the species_counts summary counts too")


def test_recorded_taxon_obscured_only_elsewhere_stays() -> None:
    details = {t["id"]: t for t in _load("inat_taxa.json")["results"]}
    _check(fs.privacy_exclusion({}, details[_ARDEA]) is None, "obscured only outside the DR should stay")


def _taxa_404(server: _Server):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404) if "/taxa/" in request.url.path else server(request)
    return handler


def test_failed_lookups_are_counted_separately() -> None:
    good, flaky = _taxon_row(1, "Testus uno", default=_allowed_photo()), _taxon_row(2, "Testus dos")
    server = _Server(
        counts={"total_results": 2, "results": [good, flaky]},
        taxa={"results": [_detail(1, "Testus uno")]},
        obis={"total": 0, "results": []},
    )
    result = _run(server)
    _check(result.excluded_lookup_failed == 1 and result.excluded_by_status == 0, "a missing detail is a failed lookup")
    _check(list(result.taxa) == [1], "the species with an answer stays")
    table = fs.format_summary({"Playa": result})
    _check("x_st" in table and "x_lk" in table, "the table shows both counts")


def test_flaky_run_cannot_empty_a_beach() -> None:
    api = _api(_taxa_404(_one_taxon_server(_taxon_row(default=_allowed_photo()))))
    try:
        fs.run_beach(api, _BEACH, _profile(), fs.Caches(), _TODAY)
    except fs.FetchError:
        pass
    else:
        raise AssertionError("every lookup failing must count as a failed beach")
    old = {"taxa": {"5": {"fetched_at": "2020-01-01"}}, "beaches": {_BEACH["name"]: [{"taxon_id": 5}]}}
    results = fs.collect(api, [_BEACH], {_BEACH["name"]: _profile()}, old, dt.date(2026, 11, 4), 25)
    _check(results == {}, "nothing to merge, so the beach's old data stays")
    _check(fs.merge_results(old, results)["beaches"] == old["beaches"], "old references are kept")


def test_skip_beaches() -> None:
    maimon = _beach("Playa Maimón")
    _check("Playa Maimón" in fs.SKIP_BEACHES and fs.SKIP_BEACHES["Playa Maimón"], "the skip has a reason")
    run, skipped = fs.split_skipped([maimon, _BEACH])
    _check(run == [_BEACH] and skipped == ["Playa Maimón"], "only the listed beach is skipped")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "auto.json"
        server = _Server()
        code, text = _main(["--beach", "Playa Maimón", "--beach", _BEACH["name"], "--output", str(path)], server)
        data = cd.read_auto_species(path)
    _check(code == 0 and "SKIPPED Playa Maimón: " in text, "skipped beaches are reported")
    _check("Playa Maimón" not in data["beaches"] and _BEACH["name"] in data["beaches"], "the skipped beach gets no species")
    lats = {r.url.params.get("lat") for r in server.paths("species_counts")}
    _check(str(maimon["latitude"]) not in lats, "no request is made for a skipped beach")


def test_failed_lookups_are_retried_for_later_beaches() -> None:
    server = _one_taxon_server(_taxon_row(default=_allowed_photo()))
    healthy = {"taxa": False, "obis": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/taxa/" in request.url.path and not healthy["taxa"]:
            return httpx.Response(404)
        if "checklist" in request.url.path and not healthy["obis"]:
            return httpx.Response(404)
        return server(request)

    api, caches = _api(handler), fs.Caches()
    try:
        fs.run_beach(api, _BEACH, _profile(), caches, _TODAY)
    except fs.FetchError:
        pass
    else:
        raise AssertionError("every lookup failing must fail the beach")
    _check(caches.details == {}, "a failed lookup must not be cached")
    healthy["taxa"] = True
    first = fs.run_beach(api, _BEACH, _profile(), caches, _TODAY)
    _check(first.excluded_lookup_failed == 0 and 1 in first.taxa, "the next beach retries the /taxa lookup")
    _check(first.taxa[1]["marine_source"] is None and caches.marine == {}, "a failed OBIS batch is unknown, not cached")
    healthy["obis"] = True
    second = fs.run_beach(api, _BEACH, _profile(), caches, _TODAY)
    _check(second.taxa[1]["marine_source"] == "obis", "the next beach retries the OBIS lookup")


def test_cached_details_keep_only_what_is_read() -> None:
    cache: dict = {}
    detail = _detail(photo_field_noise=1, ancestors=[{"x": 1}], taxon_photos=[
        {"photo": dict(_allowed_photo(), url="https://x", flags=[], medium_url="https://y"), "taxon": {"big": 1}}, "bad",
    ])
    fs.fetch_taxa_details(_api(lambda _: _json({"results": [detail]})), [1], cache)
    _check(set(cache[1]) == {"conservation_statuses", "taxon_photos"}, f"cached: {set(cache[1])}")
    _check(set(cache[1]["taxon_photos"][0]["photo"]) == set(fs.TAXON_PHOTO_FIELDS), "only the photo fields used")
    _check(cache[1]["taxon_photos"][1] == "bad", "malformed entries stay malformed, and are rejected later")
    _check(fs.choose_photo({}, cache[1])["attribution"] == "Autora", "the slimmed detail still gives a photo")
    missing: dict = {}
    fs.fetch_taxa_details(_api(lambda _: _json({"results": [{"id": 1}]})), [1], missing)
    _check(fs.privacy_exclusion({}, missing[1]) == fs.EXCLUDED_LOOKUP_FAILED, "a missing field still fails closed")


def test_request_spacing_per_host() -> None:
    waits: list[float] = []
    api = fs.Api(fs.make_client(httpx.MockTransport(lambda _: _json({"total": 0, "results": []}))), sleep=waits.append)
    fs.fetch_marine_tags(api, ["A b"], {})
    _check(waits == [fs.OBIS_DELAY_S], f"OBIS spacing: {waits}")
    waits.clear()
    fs.fetch_taxa_details(api, [1], {})
    _check(waits == [fs.INATURALIST_DELAY_S], f"iNaturalist spacing: {waits}")
    _check(fs.INATURALIST_DELAY_S >= 1, "iNaturalist asks for about one request per second")


def test_query_parameters() -> None:
    server = _Server()
    _run(server)
    params = server.paths("species_counts")[0].url.params
    _check(params["per_page"] == str(fs.SPECIES_PER_PAGE) and fs.SPECIES_PER_PAGE == 200, "per_page")
    _check(params["geoprivacy"] == "open" and params["taxon_geoprivacy"] == "open", "privacy filters")
    _check("threatened" not in params, "threatened must not be sent")
    _check(params["quality_grade"] == "research", "research grade only")
    _check(len(server.paths("species_counts")) == 1, "one species_counts request per beach")
    agent = server.requests[0].headers["user-agent"]
    _check(agent == fs.USER_AGENT and "@" not in agent, "generic User-Agent, no email")


def test_batch_sizes() -> None:
    server = _Server(taxa={"results": []})
    fs.fetch_taxa_details(_api(server), range(1, 62), {})
    sizes = [len(unquote(r.url.path).rsplit("/", 1)[1].split(",")) for r in server.paths("/taxa/")]
    _check(sizes == [30, 30, 1], f"taxa batches: {sizes}")
    server = _Server(obis={"total": 0, "results": []})
    fs.fetch_marine_tags(_api(server), [f"Testus n{i}" for i in range(25)], {})
    sizes = [len(r.url.params["scientificname"].split(",")) for r in server.requests]
    _check(sizes == [10, 10, 5], f"OBIS batches: {sizes}")


def test_caches_avoid_repeat_requests() -> None:
    server = _one_taxon_server(_taxon_row(default=_allowed_photo()))
    caches = fs.Caches()
    api = _api(server)
    fs.run_beach(api, _BEACH, _profile(), caches, _TODAY)
    fs.run_beach(api, _BEACH, _profile(), caches, _TODAY)
    _check(len(server.paths("/taxa/")) == 1, "a taxon shared by beaches is fetched once")
    _check(len(server.paths("checklist")) == 1, "a name shared by beaches is tagged once")


def test_candidate_cap_and_pins() -> None:
    rows = [_taxon_row(i, f"Testus n{i}", default=_allowed_photo(i), count=100 - i) for i in range(1, 21)]
    details = [_detail(i, f"Testus n{i}") for i in range(1, 21)]
    server = _Server(counts={"total_results": 20, "results": rows}, taxa={"results": details}, obis={"total": 0, "results": []})
    result = _run(server)
    _check(len(result.references) == fs.MAX_CANDIDATES_PER_BEACH == 12, "at most 12 candidates per beach")
    _check(result.total == 20 and result.eligible == 20, "the summary counts everything seen")
    pinned = _run(server, _profile(pinned_taxa=[20], hidden_taxa=[1]))
    ids = [ref["taxon_id"] for ref in pinned.references]
    _check(ids[0] == 20 and 1 not in ids, "pinned and hidden apply before the cap")


def test_sanitising() -> None:
    _check(fs.clean_text("  <b>Ana</b>\x07 <script>x</script> Ruiz\n", 80) == "Ana x Ruiz", "tags and control characters go")
    _check(fs.clean_text("a‮b", 80) == "a b", "bidirectional overrides go")
    _check(fs.clean_text("x" * 200, 10) == "x" * 10, "length is capped")
    for value in (None, 5, "", "  ", "<b></b>"):
        _check(fs.clean_text(value, 80) is None, f"nothing left: {value!r}")
    row = _taxon_row(name="<b>Testus</b> uno", default=dict(_allowed_photo(), attribution_name="<i>Ana</i>\x00 Ruiz"))
    row["taxon"]["preferred_common_name"] = "<script>x</script>cangrejo"
    record = _run(_one_taxon_server(row)).taxa[1]
    _check(record["scientific_name"] == "Testus uno", "scientific name cleaned")
    _check(record["photo"]["attribution"] == "Ana Ruiz", "attribution cleaned")
    _check(record["common_name"]["es"] == "X cangrejo", "common name cleaned and capitalised")
    _check(cd.taxon_problems(record) == [], "the cleaned record passes validation")


def test_malformed_rows_are_dropped() -> None:
    bad = ["x", None, {}, {"taxon": "x"}, {"count": 1, "taxon": {"id": "x", "name": "a"}},
           {"count": 0, "taxon": {"id": 2, "name": "a"}}, {"count": True, "taxon": {"id": 3, "name": "a"}},
           {"count": 1, "taxon": {"id": True, "name": "a"}}, {"count": 1, "taxon": {"id": 4, "name": ""}}]
    server = _Server(counts={"total_results": 10, "results": [*bad, _taxon_row()]}, taxa={"results": [_detail()]}, obis={"total": 0, "results": []})
    result = _run(server)
    _check(list(result.taxa) == [1], "only the good row survives")
    for payload in ({"results": "x"}, {}):
        try:
            _run(_Server(counts=payload or {"nope": 1}))
        except fs.FetchError:
            continue
        raise AssertionError(f"should fail: {payload}")


def test_no_coordinates_in_output() -> None:
    result = _run(_Server())
    text = fs.serialise(fs.merge_results({"taxa": {}, "beaches": {}}, {_BEACH["name"]: result}))
    for word in ("latitude", "longitude", '"lat"', '"lng"', "lat=", "lng=", "location", "geojson", "private_location"):
        _check(word not in text, f"coordinates in the output: {word}")
    _check(str(_BEACH["latitude"]) not in text and str(_BEACH["longitude"]) not in text, "beach coordinates in the output")


def test_deterministic_output() -> None:
    def build() -> str:
        return fs.serialise(fs.merge_results({"taxa": {}, "beaches": {}}, {_BEACH["name"]: _run(_Server())}))

    first = build()
    _check(first == build(), "the same input must give the same bytes")
    payload = json.loads(first)
    _check(list(payload) == sorted(payload), "sorted keys")


def test_merge_keeps_other_beaches_and_prunes() -> None:
    old = {
        "taxa": {"1": {"taxon_id": 1}, "2": {"taxon_id": 2}},
        "beaches": {"Otra": [{"taxon_id": 1, "observation_count": 1}], _BEACH["name"]: [{"taxon_id": 2, "observation_count": 1}]},
    }
    merged = fs.merge_results(old, {_BEACH["name"]: _run(_one_taxon_server(_taxon_row(9), _detail(9)))})
    _check("Otra" in merged["beaches"] and "1" in merged["taxa"], "other beaches stay")
    _check("2" not in merged["taxa"] and "9" in merged["taxa"], "unused species are dropped")


def _beach(name: str) -> dict:
    return next(b for b in BEACHES if b["name"] == name)


def test_failed_beach_does_not_stop_the_run() -> None:
    server = _one_taxon_server()
    good, bad = _beach("Playa Bávaro"), _beach("Playa Macao")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/species_counts") and request.url.params["lat"] == str(bad["latitude"]):
            return httpx.Response(400)
        return server(request)

    profiles = {good["name"]: _profile(), bad["name"]: _profile()}
    results = fs.collect(_api(handler), [bad, good], profiles, {"taxa": {}, "beaches": {}}, dt.date(2026, 11, 4), 25)
    _check(list(results) == [good["name"]], "the other beach still runs")


def test_max_age() -> None:
    existing = {"taxa": {"1": {"fetched_at": "2026-11-01"}}, "beaches": {"A": [{"taxon_id": 1}]}}
    today = dt.date(2026, 11, 4)
    _check(fs.is_fresh(existing, "A", today, 25), "recent is fresh")
    _check(not fs.is_fresh(existing, "A", dt.date(2027, 1, 1), 25), "old is stale")
    _check(not fs.is_fresh(existing, "B", today, 25), "unknown is stale")
    _check(not fs.is_fresh({"taxa": {}, "beaches": {"A": [{"taxon_id": 1}]}}, "A", today, 25), "missing taxon is stale")
    _check(not fs.is_fresh({"taxa": {"1": {"fetched_at": "x"}}, "beaches": {"A": [{"taxon_id": 1}]}}, "A", today, 25), "bad date is stale")
    server = _one_taxon_server()
    fresh = {"taxa": {"1": {"fetched_at": today.isoformat()}}, "beaches": {_BEACH["name"]: [{"taxon_id": 1}]}}
    _check(fs.collect(_api(server), [_BEACH], {_BEACH["name"]: _profile()}, fresh, today, 25) == {}, "a fresh beach is skipped")
    _check(server.requests == [], "a fresh beach makes no requests")


def test_response_cap_applies_everywhere() -> None:
    _check(fs.MAX_RESPONSE_BYTES == 4 * 1024 * 1024, "the cap is 4 MB")
    huge = b'{"results": "' + b"x" * (fs.MAX_RESPONSE_BYTES + 1) + b'"}'
    api = _api(lambda _: httpx.Response(200, content=huge))
    for url in (fs.INAT_SPECIES_COUNTS_URL, f"{fs.INAT_TAXA_URL}/1", fs.OBIS_CHECKLIST_URL):
        try:
            fs.get_json(api, url)
        except fs.FetchError as exc:
            _check("cap" in str(exc), f"wrong failure: {exc}")
            continue
        raise AssertionError(f"oversized response accepted from {url}")
    error = _api(lambda _: httpx.Response(500, content=huge))
    try:
        fs.get_json(error, fs.OBIS_CHECKLIST_URL)
    except fs.FetchError as exc:
        _check("cap" in str(exc), "the cap applies to error responses too")
    else:
        raise AssertionError("oversized error response accepted")


def test_retry_rules() -> None:
    calls, waits = [], []

    def flaky(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return _json({"ok": True})

    api = fs.Api(fs.make_client(httpx.MockTransport(flaky)), sleep=waits.append)
    _check(fs.get_json(api, "https://example.org/x") == {"ok": True}, "a retry should succeed")
    _check(any(7 <= wait < 8 for wait in waits), f"Retry-After must be respected: {waits}")
    for status, expected in ((503, fs.MAX_ATTEMPTS), (404, 1), (400, 1)):
        calls.clear()

        def failing(request: httpx.Request, status: int = status) -> httpx.Response:
            calls.append(request)
            return httpx.Response(status)

        try:
            fs.get_json(_api(failing), "https://example.org/x")
        except fs.FetchError:
            pass
        else:
            raise AssertionError(f"HTTP {status} should fail")
        _check(len(calls) == expected, f"HTTP {status}: {len(calls)} attempts, expected {expected}")


def test_redirects_not_followed_and_json_only() -> None:
    calls = []

    def redirect(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.test/"})

    for handler, label in ((redirect, "redirect"), (lambda _: httpx.Response(200, text="<html>"), "not JSON"), (lambda _: httpx.Response(200, text="[]"), "not an object")):
        try:
            fs.get_json(_api(handler), "https://example.org/x")
        except fs.FetchError:
            continue
        raise AssertionError(f"should fail: {label}")
    _check(len(calls) == 1 and "evil.test" not in str(calls[0].url), "redirects must not be followed")


def test_atomic_write_keeps_the_old_file() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "auto.json"
        fs.write_atomic(path, "first")
        _check(path.read_text() == "first", "the write should land")
        with mock.patch.object(fs.os, "replace", side_effect=OSError("disk")):
            try:
                fs.write_atomic(path, "second")
            except OSError:
                pass
            else:
                raise AssertionError("the failure should surface")
        _check(path.read_text() == "first", "a failed write must leave the old file intact")
        _check([p.name for p in Path(folder).iterdir()] == ["auto.json"], "no temp file left behind")


def _main(argv: list[str], server: _Server) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = fs.main(argv, transport=httpx.MockTransport(server), sleep=lambda _: None)
    return code, out.getvalue()


def test_dry_run_writes_nothing_and_reports() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "auto.json"
        code, text = _main(["--dry-run", "--beach", _BEACH["name"], "--output", str(path)], _Server())
        _check(code == 0 and not path.exists(), "a dry run must not write")
    for column in ("p_def", "p_tax", "x_st", "x_lk", "marine", "top 3"):
        _check(column in text, f"summary is missing {column}")
    _check(_BEACH["name"] in text, "summary lists the beach")


def test_real_run_writes_a_loadable_file() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "auto.json"
        code, _ = _main(["--beach", _BEACH["name"], "--output", str(path)], _Server())
        _check(code == 0, "the run should succeed")
        data = cd.read_auto_species(path)
        _check(_BEACH["name"] in data["beaches"], "the file should load back")
        species = cd.build_index({}, {}, data)[_BEACH["name"]]["species"]
        _check(species and all(s["sources"] for s in species), "every species has a source")
        for s in species:
            _check(cd.is_safe_url(s["sources"][0]["url"]), "source links pass the URL check")
        with mock.patch.object(fs, "MAX_AUTO_SPECIES_BYTES", 10):
            before = path.read_text(encoding="utf-8")
            code, _ = _main(["--beach", "Playa Macao", "--output", str(path)], _Server())
        _check(code == 1 and path.read_text(encoding="utf-8") == before, "over budget: refuse to write")


def test_unknown_beach_is_refused() -> None:
    try:
        fs._select_beaches(["Playa inexistente"])
    except SystemExit:
        return
    raise AssertionError("an unknown beach should stop the run")


def main() -> int:
    logging.disable(logging.CRITICAL)
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    for name, test in tests:
        test()
        print(f"  ok {name}")
    print(f"All {len(tests)} fetch_species tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
