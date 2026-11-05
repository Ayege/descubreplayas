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

"""Offline tests for the Commons photo scripts, using fixture API responses.

Needs Pillow 13, so run it with the ingest environment, from the repo root:

    .venv-ingest/bin/python -m tests.test_beach_photos
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import io
import json
import logging
import sys
import tempfile
from pathlib import Path

import httpx
from PIL import Image

from dashboard.conoce_data import beach_photo_problems, is_safe_url
from scripts import _commons as commons
from scripts import beach_photo_candidates as candidates
from scripts import import_beach_photo as importer
from scripts._http import Api, RateLimited, make_client
from scripts._images import MAX_DOWNLOAD_URL_CHARS, download

_TODAY = dt.date(2026, 11, 5)
_FIXTURE = Path(__file__).parent / "fixtures" / "commons_imageinfo.json"
_BEACH = "Playa Bávaro"
_TITLE = "File:Beach.jpg"
_THUMB = "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Beach.jpg/500px-Beach.jpg?utm_source=x"
_DOWNLOAD = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Beach.jpg/500px-Beach.jpg"
_PAGE_URL = "https://commons.wikimedia.org/wiki/File:Beach.jpg"


def _check(condition: bool, message: str) -> None:
    assert condition, message


def _page(**overrides: object) -> dict:
    """A page shaped like the real one in the fixture. None removes a field."""
    metadata = {
        "LicenseShortName": {"value": "CC BY-SA 4.0"},
        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
        "Artist": {"value": '<a href="https://x.example">Ana Pérez</a>'},
        "DateTimeOriginal": {"value": "Taken on 23 December 2008"},
    }
    info = {
        "width": 3000, "height": 2000, "mime": "image/jpeg", "thumburl": _THUMB, "thumbwidth": 500,
        "thumbheight": 333, "descriptionurl": _PAGE_URL, "extmetadata": metadata,
    }
    for key, value in overrides.items():
        target = metadata if key in metadata else info
        if value is None:
            target.pop(key, None)
        else:
            target[key] = {"value": value} if key in metadata else value
    return {"ns": 6, "title": _TITLE, "index": 1, "imageinfo": [info]}


def _jpeg(size: tuple[int, int] = (500, 333)) -> bytes:
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[0x010E] = "SECRET-METADATA"
    Image.effect_noise(size, 10).convert("RGB").save(buffer, "JPEG", quality=70, exif=exif)
    return buffer.getvalue()


class _Server:
    """Answers API and image requests, and records them."""

    def __init__(self, pages: list | None = None, image: bytes | None = None) -> None:
        self.pages = pages if pages is not None else [_page()]
        self.image = image if image is not None else _jpeg()
        self.requests: list[httpx.Request] = []
        self.api_responses: list[httpx.Response] = []
        self.image_response: httpx.Response | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "commons.wikimedia.org":
            if self.api_responses:
                return self.api_responses.pop(0)
            return httpx.Response(200, json={"query": {"pages": self.pages}})
        if self.image_response is not None:
            return self.image_response
        return httpx.Response(200, content=self.image, headers={"Content-Type": "image/jpeg"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def hosts(self) -> set[str]:
        return {request.url.host for request in self.requests}


def _api(server: _Server, sleeps: list[float] | None = None) -> Api:
    return Api(make_client(server.transport), (sleeps if sleeps is not None else []).append)


# --- Licences -------------------------------------------------------------------------

def test_exact_licences_allowed() -> None:
    cases = {
        ("CC0", "https://creativecommons.org/publicdomain/zero/1.0/deed.en"): ("cc0", "1.0"),
        ("CC0 1.0", "http://creativecommons.org/publicdomain/zero/1.0"): ("cc0", "1.0"),
        ("CC BY 3.0", "https://creativecommons.org/licenses/by/3.0"): ("cc-by", "3.0"),
        ("CC BY 2.5", "http://creativecommons.org/licenses/by/2.5/"): ("cc-by", "2.5"),
        ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/legalcode"): ("cc-by-sa", "4.0"),
    }
    for (name, url), (code, version) in cases.items():
        decision = commons.decide_license(name, url)
        _check(decision.license is not None and decision.license.code == code, f"should allow {name}")
        _check(version in decision.license.url and decision.license.url.startswith("https://"), f"url for {name}")


def test_disallowed_licences_refused_without_review() -> None:
    cases = [
        ("Public domain", "https://creativecommons.org/publicdomain/mark/1.0/"),
        ("Public domain", None),
        ("GFDL", "https://www.gnu.org/copyleft/fdl.html"),
        ("GFDL", None),
        ("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0"),
        ("CC BY-ND 4.0", "https://creativecommons.org/licenses/by-nd/4.0"),
        ("CC BY-SA 3.0 es", "https://creativecommons.org/licenses/by-sa/3.0/es"),
        ("All rights reserved", None),
        (None, None),
        (3, ["x"]),
    ]
    for name, url in cases:
        decision = commons.decide_license(name, url)
        _check(decision.license is None and not decision.needs_manual_check, f"should refuse {name}")


def test_disagreeing_licence_fields_need_manual_check() -> None:
    cases = [
        ("Public domain", "https://creativecommons.org/licenses/by-sa/3.0"),
        ("CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/4.0"),
        ("CC BY 4.0", "https://creativecommons.org/licenses/by-sa/4.0"),
        ("CC BY-SA 3.0", None),
        ("CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/3.0/es"),
        ("CC0", "https://creativecommons.org/licenses/by/4.0"),
        ("CC BY 4.0", "https://evil.example/licenses/by/4.0"),
    ]
    for name, url in cases:
        decision = commons.decide_license(name, url)
        _check(decision.license is None and decision.needs_manual_check, f"should need a check: {name} {url}")
    result = commons.parse_page(_page(LicenseShortName="Public domain"), _TODAY)
    _check(isinstance(result, commons.ManualCheck), "a page with disagreeing fields is listed for review")


def test_only_the_licence_in_use_is_stored() -> None:
    result = commons.parse_page(_page(), _TODAY)
    _check(result.license == commons.License("cc-by-sa", "CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
           "one licence, with our own canonical URL")


# --- Author and year ------------------------------------------------------------------

def test_artist_html_is_stripped() -> None:
    cases = {
        '<a rel="nofollow" href="https://x">Kirill Flerov</a>': "Kirill Flerov",
        '<span class="fn"><bdi>Ana &amp; Luis</bdi></span>': "Ana & Luis",
        "<script>alert(1)</script>Ana": "Ana",
        "Ana\n  <b>Pérez</b>": "Ana Pérez",
    }
    for raw, expected in cases.items():
        _check(commons.parse_author(raw) == expected, f"{raw!r} gave {commons.parse_author(raw)!r}")
    escaped = commons.parse_author("&lt;img src=x onerror=alert(1)&gt;Ana")
    _check(escaped is not None and "<" not in escaped and ">" not in escaped, "decoded tags must not survive")
    for empty in (None, "", "<a href='x'></a>", 3, "x" * 121):
        _check(commons.parse_author(empty) is None, f"{empty!r} is not an author")


def test_year_parsing() -> None:
    cases = {
        "Taken on 23 December 2008": 2008,
        "2008-12-23 10:30:00": 2008,
        "2008:12:23 10:30:00": 2008,
        '<time class="dtstart" datetime="1999-01-01">2008</time>': 2008,
        "2008": 2008,
        "1900": 1900,
        "2026": 2026,
        "1899": None,
        "2027": None,
        "2008-2010": None,
        "2008, 2010": None,
        "circa 2008": None,
        "c. 2008": None,
        "before 1950": None,
        "2000s": None,
        "2008?": None,
        "20081223": None,
        "unknown": None,
        "": None,
        None: None,
        12: None,
    }
    for raw, expected in cases.items():
        _check(commons.parse_year(raw, _TODAY) == expected, f"{raw!r} gave {commons.parse_year(raw, _TODAY)}")


# --- Thumbnails -----------------------------------------------------------------------

def test_thumbnail_goes_to_the_one_download_host() -> None:
    _check(commons.thumbnail_download_url(_THUMB, 500) == _DOWNLOAD, "moved to upload, tracking query dropped")
    _check(commons.DOWNLOAD_HOSTS == frozenset({"upload.wikimedia.org"}), "exactly one download host")


def test_download_width_is_standard_and_above_the_stored_width() -> None:
    _check(commons.DOWNLOAD_WIDTH in commons.STANDARD_THUMB_WIDTHS, "500 must be a standard width")
    _check(commons.DOWNLOAD_WIDTH > commons.MAX_BEACH_PHOTO_WIDTH, "we shrink it, never enlarge it")


def test_non_standard_widths_are_reported() -> None:
    for width in (480, 501, 250, 330):  # 250 and 330 are standard but too small to store.
        url = _THUMB.replace("500px", f"{width}px")
        try:
            commons.thumbnail_download_url(url, width)
        except commons.ThumbnailRefused as exc:
            _check(exc.reason == commons.REASON_NON_STANDARD_WIDTH, f"reason for {width}")
        else:
            raise AssertionError(f"{width} should be refused")
    result = commons.parse_page(_page(thumburl=_THUMB.replace("500px", "480px"), thumbwidth=480), _TODAY)
    _check(result == commons.Refusal(commons.REASON_NON_STANDARD_WIDTH), "the page is skipped and counted")
    result = commons.parse_page(_page(thumbwidth=480), _TODAY)
    _check(result == commons.Refusal(commons.REASON_NON_STANDARD_WIDTH), "the URL and field must agree")


def test_long_file_names_still_work() -> None:
    name = "Praia_de_Punta_Cana%2C_Rep%C3%BAblica_Dominicana._-_panoramio_-_Cid_Penteado_Jr_%281%29.jpg" * 2
    url = f"https://thumb.wikimedia.org/wikipedia/commons/thumb/f/fa/{name}/500px-{name}?utm_source=x"
    _check(len(url) > commons.is_safe_url.__defaults__[0], "this URL must be over the limit for stored URLs")
    _check(commons.thumbnail_download_url(url, 500).endswith(f"/500px-{name}"), "a long name isn't a bad URL")
    _check(not is_safe_url("https://x.example/" + "a" * 3000, MAX_DOWNLOAD_URL_CHARS), "there is still a limit")
    server = _Server()
    download(_api(server), commons.thumbnail_download_url(url, 500), commons.DOWNLOAD_HOSTS)
    _check(server.hosts() == {"upload.wikimedia.org"}, "and the long URL can be downloaded")


def test_bad_thumbnail_urls_fail_closed() -> None:
    bad = [
        None, "", 5,
        "http://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/B.jpg/500px-B.jpg",
        "https://evil.example/wikipedia/commons/thumb/a/ab/B.jpg/500px-B.jpg",
        "https://thumb.wikimedia.org.evil.example/wikipedia/commons/thumb/a/ab/B.jpg/500px-B.jpg",
        "https://user@thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/B.jpg/500px-B.jpg",
        "https://thumb.wikimedia.org:8443/wikipedia/commons/thumb/a/ab/B.jpg/500px-B.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/../500px-B.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/a/ab/B.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/B.jpg/lossy-page1-500px-B.jpg",
    ]
    for url in bad:
        try:
            commons.thumbnail_download_url(url, 500)
        except commons.ThumbnailRefused as exc:
            _check(exc.reason == commons.REASON_NO_THUMBNAIL, f"reason for {url}")
        else:
            raise AssertionError(f"{url} should be refused")
    _check(commons.parse_page(_page(thumburl=None), _TODAY) == commons.Refusal(commons.REASON_NO_THUMBNAIL),
           "a missing thumburl skips the candidate")


# --- Candidate filtering --------------------------------------------------------------

def test_real_response_shape_parses() -> None:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = commons.parse_page(payload["query"]["pages"][0], _TODAY)
    _check(isinstance(result, commons.Candidate), f"the live shape should pass, got {result}")
    _check(result.author == "Kirill Flerov" and result.year == 2008, "author and year from the live record")
    _check(result.license.code == "cc-by" and result.license.label == "CC BY 3.0", "licence from the live record")
    _check(result.thumbnail_url.startswith("https://upload.wikimedia.org/") and "?" not in result.thumbnail_url, "host")
    _check("?" not in result.source_url and result.source_url.startswith("https://commons.wikimedia.org/wiki/File:"), "page")


def test_filters_type_and_width() -> None:
    _check(commons.parse_page(_page(mime="image/tiff"), _TODAY) == commons.Refusal(commons.REASON_MIME), "tiff")
    _check(commons.parse_page(_page(mime="image/svg+xml"), _TODAY) == commons.Refusal(commons.REASON_MIME), "svg")
    _check(commons.parse_page(_page(width=799), _TODAY) == commons.Refusal(commons.REASON_TOO_NARROW), "799 px")
    _check(isinstance(commons.parse_page(_page(width=800), _TODAY), commons.Candidate), "800 px is enough")
    for page in (None, {}, {"title": _TITLE}, {"title": _TITLE, "imageinfo": []}, {"title": _TITLE, "imageinfo": ["x"]}):
        _check(commons.parse_page(page, _TODAY) == commons.Refusal(commons.REASON_NO_INFO), f"malformed {page}")
    bad_source = commons.parse_page(_page(descriptionurl="https://evil.example/wiki/File:B.jpg"), _TODAY)
    _check(bad_source == commons.Refusal(commons.REASON_BAD_SOURCE), "the file page must be on Commons")
    _check(commons.parse_page(_page(Artist=None), _TODAY) == commons.Refusal(commons.REASON_NO_AUTHOR), "no author")


def test_list_is_capped_and_closest_first() -> None:
    pages = [dict(_page(), title=f"File:{i}.jpg", index=20 - i) for i in range(15)]
    server = _Server(pages)
    report = candidates.build_report(_BEACH, commons.fetch_nearby(_api(server), 18.0, -68.0), _TODAY)
    _check(len(report.candidates) == candidates.MAX_CANDIDATES_PER_BEACH, "at most 10")
    _check(report.candidates[0].title == "File:14.jpg", "closest first, by the API's index")
    _check(report.refused["over the per-beach list limit"] == 5, "the rest are counted")


def test_query_parameters_are_the_approved_ones() -> None:
    server = _Server()
    commons.fetch_nearby(_api(server), 18.68, -68.43)
    params = dict(server.requests[0].url.params)
    expected = {
        "action": "query", "format": "json", "formatversion": "2", "maxlag": "5", "generator": "geosearch",
        "ggscoord": "18.68|-68.43", "ggsradius": "2000", "ggslimit": "50", "ggsnamespace": "6",
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "500",
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|DateTimeOriginal",
    }
    _check(params == expected, f"unexpected parameters: {params}")
    agent = server.requests[0].headers["User-Agent"]
    _check("github.com/Ayege/descubreplayas" in agent and "@" not in agent, "repo URL, no email")


def test_markdown_escapes_untrusted_text() -> None:
    page = _page(Artist="[x](javascript:alert(1)) | `y`\n# z")
    report = candidates.build_report(_BEACH, [dict(page, title="File:a|b]<c>.jpg")], _TODAY)
    out = io.StringIO()
    candidates.format_markdown([report], ["Playa Maimón"], out)
    text = out.getvalue()
    _check("\\[x\\](javascript" in text and "\\|" in text, "the author can't make a link or a new cell")
    _check("a\\|b\\]" in text and "<c>" not in text, "the title is escaped")
    _check("Skipped: coordinates point to the wrong coast" in text, "a skipped beach is reported")


def test_manual_checks_are_listed_in_the_file() -> None:
    report = candidates.build_report(_BEACH, [_page(LicenseShortName="Public domain")], _TODAY)
    out = io.StringIO()
    candidates.format_markdown([report], [], out)
    text = out.getvalue()
    _check("Needs manual check" in text and "Public domain" in text, "the file is listed for a person")
    _check(not report.candidates, "and it is not a candidate")


# --- Rate limits and maxlag -----------------------------------------------------------

def test_429_is_not_retried() -> None:
    server = _Server()
    server.api_responses = [httpx.Response(429), httpx.Response(429)]
    sleeps: list[float] = []
    try:
        commons.fetch_nearby(_api(server, sleeps), 18.0, -68.0)
    except RateLimited:
        pass
    else:
        raise AssertionError("a 429 must raise")
    _check(len(server.requests) == 1, "one request, no retry loop")


def test_429_stops_that_beach_then_the_run() -> None:
    server = _Server()
    server.api_responses = [httpx.Response(429), httpx.Response(200, json={"query": {"pages": []}}),
                            httpx.Response(429), httpx.Response(429)]
    beaches = [{"name": f"B{i}", "latitude": 18.0, "longitude": -68.0} for i in range(6)]
    reports = candidates.collect(_api(server), beaches, _TODAY)
    _check("429" in reports[0].error and reports[1].error is None, "a 429 stops only that beach")
    _check("429" in reports[2].error and "429" in reports[3].error, "the next beaches are still tried")
    _check(all("not attempted" in report.error for report in reports[4:]), "two in a row stop the run")
    _check(len(server.requests) == 4, "and no more requests go out")


def test_download_429_is_not_retried() -> None:
    server = _Server()
    server.image_response = httpx.Response(429)
    with tempfile.TemporaryDirectory() as tmp:
        code, _out, err = _run_import(server, Path(tmp), [])
        _check(code == 1 and "429" in err, "reported")
        _check(sum(r.url.host == "upload.wikimedia.org" for r in server.requests) == 1, "one download attempt")
        _check(not (Path(tmp) / "photos.json").exists(), "nothing written")


def test_maxlag_honours_retry_after() -> None:
    server = _Server()
    server.api_responses = [httpx.Response(200, json={"error": {"code": "maxlag"}}, headers={"Retry-After": "7"})]
    sleeps: list[float] = []
    pages = commons.fetch_nearby(_api(server, sleeps), 18.0, -68.0)
    _check(len(pages) == 1 and len(server.requests) == 2, "retried once, then answered")
    _check(any(7 <= wait < 8 for wait in sleeps), f"should wait about 7 s: {sleeps}")


def test_maxlag_gives_up_and_other_errors_stop() -> None:
    server = _Server()
    server.api_responses = [httpx.Response(200, json={"error": {"code": "maxlag"}}) for _ in range(5)]
    reports = candidates.collect(_api(server), [{"name": "B", "latitude": 1.0, "longitude": 2.0}], _TODAY)
    _check(reports[0].error == "FetchError" and len(server.requests) == commons.MAXLAG_ATTEMPTS, "bounded")
    server = _Server()
    server.api_responses = [httpx.Response(200, json={"error": {"code": "badvalue"}})]
    reports = candidates.collect(_api(server), [{"name": "B", "latitude": 1.0, "longitude": 2.0}], _TODAY)
    _check(reports[0].error == "FetchError" and len(server.requests) == 1, "other errors aren't retried")


def test_candidates_script_writes_only_its_output() -> None:
    server = _Server()
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "candidates.md"
        with contextlib.redirect_stdout(io.StringIO()):
            code = candidates.main(["--beach", _BEACH, "--output", str(output)], server.transport, lambda _s: None)
        _check(code == 0 and "File:Beach.jpg" in output.read_text(encoding="utf-8"), "the file is written")
        _check([path.name for path in Path(tmp).iterdir()] == ["candidates.md"], "nothing else")
        _check(server.hosts() == {"commons.wikimedia.org"}, "no image is downloaded")
    server = _Server()
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "candidates.md"
        with contextlib.redirect_stdout(io.StringIO()):
            code = candidates.main(["--beach", "Playa Maimón", "--output", str(output)], server.transport, lambda _s: None)
        _check(code == 0 and not server.requests, "Maimón is skipped with no request")
        _check("Skipped" in output.read_text(encoding="utf-8"), "and reported")


# --- Import ---------------------------------------------------------------------------

def _run_import(server: _Server, root: Path, extra: list[str], alt: bool = True) -> tuple[int, str, str]:
    args = ["--beach", _BEACH, "--output", str(root / "photos.json"), "--static-dir", str(root / "static"), *extra]
    if "--own" not in extra and "--commons-file" not in extra:
        args += ["--commons-file", _TITLE]
    if alt:
        args += ["--alt-es", "Palmeras y arena blanca", "--alt-en", "Palm trees and white sand"]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = importer.main(args, server.transport, lambda _s: None)
    return code, out.getvalue(), err.getvalue()


def test_import_writes_a_valid_entry_without_metadata() -> None:
    server = _Server()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        code, out, _err = _run_import(server, root, [])
        _check(code == 0, "import should pass")
        entry = json.loads((root / "photos.json").read_text(encoding="utf-8"))[_BEACH]
        _check(beach_photo_problems(entry) == [], "the dashboard must accept the entry")
        _check(entry["width"] == 480, "500 px download, 480 px stored")
        _check(entry["author"] == "Ana Pérez" and entry["license"] == "cc-by-sa" and entry["taken_year"] == 2008, "credit")
        _check(entry["source_url"] == _PAGE_URL and entry["source"] == "wikimedia_commons", "source")
        data = (root / "static" / entry["file"]).read_bytes()
        _check(b"SECRET-METADATA" not in data and not Image.open(io.BytesIO(data)).getexif(), "no metadata survives")
        for line in importer.CHECKLIST.splitlines():
            _check(line in out, f"checklist line missing: {line}")
        _check({r.url.host for r in server.requests} == {"commons.wikimedia.org", "upload.wikimedia.org"}, "hosts")
        _check(server.requests[-1].url.query == b"", "no tracking query on the download")


def test_import_output_is_deterministic() -> None:
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        image = _jpeg()
        for tmp in (tmp_a, tmp_b):
            _run_import(_Server(image=image), Path(tmp), [])
        first, second = (Path(tmp, "photos.json").read_bytes() for tmp in (tmp_a, tmp_b))
        _check(first == second and first.endswith(b"\n"), "same input, same bytes")


def test_import_refetches_the_licence() -> None:
    server = _Server([_page(LicenseShortName="CC BY-NC 4.0", LicenseUrl="https://creativecommons.org/licenses/by-nc/4.0")])
    with tempfile.TemporaryDirectory() as tmp:
        code, _out, err = _run_import(server, Path(tmp), [])
        _check(code == 1 and "licence" in err, "a licence that changed is refused")
        _check(server.hosts() == {"commons.wikimedia.org"}, "before any download")
        _check(not (Path(tmp) / "photos.json").exists(), "nothing written")
    server = _Server([_page(LicenseShortName="Public domain")])
    with tempfile.TemporaryDirectory() as tmp:
        code, _out, err = _run_import(server, Path(tmp), [])
        _check(code == 1 and "disagree" in err, "disagreeing fields are refused here too")


def test_import_refuses_missing_alt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _run_import(_Server(), Path(tmp), [], alt=False)
        except SystemExit as exc:
            _check(exc.code == 2, "argparse refuses it")
        else:
            raise AssertionError("alt text is required")
        server = _Server()
        args = ["--beach", _BEACH, "--commons-file", _TITLE, "--alt-es", "  ", "--alt-en", "ok",
                "--output", str(Path(tmp) / "photos.json"), "--static-dir", str(Path(tmp) / "static")]
        with contextlib.redirect_stderr(io.StringIO()):
            _check(importer.main(args, server.transport, lambda _s: None) == 1, "blank alt is refused")
        _check(not server.requests, "before any request")


def test_import_refuses_unknown_beach() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        server = _Server()
        args = ["--beach", "Playa Inventada", "--commons-file", _TITLE, "--alt-es", "a", "--alt-en", "b",
                "--output", str(Path(tmp) / "photos.json"), "--static-dir", str(Path(tmp) / "static")]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            _check(importer.main(args, server.transport, lambda _s: None) == 1, "refused")
        _check("unknown beach" in err.getvalue() and not server.requests, "before any request")


def test_import_refuses_a_different_file_in_the_response() -> None:
    server = _Server([dict(_page(), title="File:Other.jpg")])
    with tempfile.TemporaryDirectory() as tmp:
        code, _out, _err = _run_import(server, Path(tmp), [])
        _check(code == 1 and server.hosts() == {"commons.wikimedia.org"}, "the answer must be about the file we asked for")


def test_import_refuses_an_empty_title() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        server = _Server()
        code, _out, err = _run_import(server, Path(tmp), ["--commons-file", ""])
        _check(code == 1 and "--commons-file" in err and not server.requests, "refused, without a crash")


def test_import_refuses_a_second_title() -> None:
    for title in ("File:A.jpg|File:B.jpg", "Beach.jpg", "Special:Export", "File:A<b>.jpg", "File:", "File:A\n.jpg"):
        _check(commons.normalise_title(title) is None, f"{title!r} should be refused")
    _check(commons.normalise_title("File:my_beach.jpg") == "File:My beach.jpg", "normalised as Commons does")


def test_import_replaces_only_with_flag() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _check(_run_import(_Server(), root, [])[0] == 0, "first import")
        path = root / "photos.json"
        before = path.read_bytes()
        old = json.loads(before)[_BEACH]["file"]
        server = _Server(image=_jpeg((500, 300)))
        code, _out, err = _run_import(server, root, [])
        _check(code == 1 and "--replace" in err and path.read_bytes() == before, "refused without the flag")
        _check(not server.requests, "before any request")
        code, _out, _err = _run_import(_Server(image=_jpeg((500, 300))), root, ["--replace"])
        new = json.loads(path.read_text(encoding="utf-8"))[_BEACH]["file"]
        _check(code == 0 and new != old, "replaced with the flag")
        _check((root / "static" / new).exists() and not (root / "static" / old).exists(), "the old file is removed")


def test_replacing_keeps_a_file_another_beach_uses() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _run_import(_Server(), root, [])
        path = root / "photos.json"
        photos = json.loads(path.read_text(encoding="utf-8"))
        shared = photos[_BEACH]["file"]
        photos["Otra"] = copy.deepcopy(photos[_BEACH])
        path.write_text(json.dumps(photos), encoding="utf-8")
        _run_import(_Server(image=_jpeg((500, 300))), root, ["--replace"])
        _check((root / "static" / shared).exists(), "still in use elsewhere")


def test_replacing_never_deletes_outside_the_pattern() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "static").mkdir()
        victim = root / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        (root / "photos.json").write_text(json.dumps({_BEACH: {"file": "../victim.txt"}}), encoding="utf-8")
        _check(_run_import(_Server(), root, ["--replace"])[0] == 0, "import")
        _check(victim.exists(), "a path in the JSON can't delete anything else")


def test_damaged_photo_file_is_not_overwritten() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "photos.json"
        path.write_text("{not json", encoding="utf-8")
        code, _out, err = _run_import(_Server(), root, [])
        _check(code == 1 and path.read_text(encoding="utf-8") == "{not json", "other beaches' entries must survive")
        _check("can't be read" in err, "explained")


def test_budget_is_enforced_before_writing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "static").mkdir()
        (root / "static" / "big").write_bytes(b"x" * importer.MAX_STATIC_BYTES)
        code, _out, err = _run_import(_Server(), root, [])
        _check(code == 1 and "budget" in err and not (root / "photos.json").exists(), "over budget")


def test_download_is_refused_when_not_an_image() -> None:
    server = _Server()
    server.image_response = httpx.Response(200, content=b"<html>", headers={"Content-Type": "text/html"})
    with tempfile.TemporaryDirectory() as tmp:
        code, _out, _err = _run_import(server, Path(tmp), [])
        _check(code == 1 and not (Path(tmp) / "photos.json").exists(), "refused")
    server = _Server()
    server.image_response = httpx.Response(302, headers={"Location": "https://evil.example/x.jpg"})
    with tempfile.TemporaryDirectory() as tmp:
        code, _out, _err = _run_import(server, Path(tmp), [])
        _check(code == 1 and "evil.example" not in server.hosts(), "a redirect is refused, not followed")


def test_own_photo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        photo = root / "mine.jpg"
        photo.write_bytes(_jpeg((1200, 800)))
        server = _Server()
        code, _out, _err = _run_import(server, root, ["--own", str(photo), "--year", "2024"])
        _check(code == 0 and not server.requests, "own photos use no network")
        entry = json.loads((root / "photos.json").read_text(encoding="utf-8"))[_BEACH]
        _check(entry["author"] == importer.OWN_PHOTO_AUTHOR and entry["license"] == "cc-by", "credit from constants")
        _check(entry["source"] == "own" and entry["taken_year"] == 2024 and entry["width"] == 480, "fields")
        _check(beach_photo_problems(entry) == [], "valid")
        code, _out, err = _run_import(_Server(), root, ["--own", str(photo), "--year", "1800", "--replace"])
        _check(code == 1 and "--year" in err, "an impossible year is refused")
        photo.write_bytes(b"not an image")
        code, _out, _err = _run_import(_Server(), root, ["--own", str(photo), "--year", "2024", "--replace"])
        _check(code == 1, "a file that isn't an image is refused")
        code, _out, _err = _run_import(_Server(), root, ["--own", str(root / "none.jpg"), "--year", "2024", "--replace"])
        _check(code == 1, "a missing file is refused")


def test_year_arguments_match_the_origin() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for extra in (["--own", "x.jpg"], ["--commons-file", _TITLE, "--year", "2020"]):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    _run_import(_Server(), Path(tmp), extra)
            except SystemExit as exc:
                _check(exc.code == 2, "refused by the parser")
            else:
                raise AssertionError(f"{extra} should be refused")


def main() -> int:
    logging.disable(logging.CRITICAL)
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    for name, test in tests:
        test()
        print(f"  ok {name}")
    print(f"All {len(tests)} beach photo tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
