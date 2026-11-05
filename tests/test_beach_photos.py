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

import argparse
import contextlib
import copy
import datetime as dt
import html
import io
import json
import logging
import re
import shlex
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

import httpx
from PIL import Image

from dashboard.beaches_data import BEACHES
from dashboard.conoce_data import beach_photo_problems, is_safe_url
from scripts import _beach_match as match
from scripts import _commons as commons
from scripts import beach_photo_candidates as candidates
from scripts import import_beach_photo as importer
from scripts._http import Api, RateLimited, make_client
from scripts._images import MAX_DOWNLOAD_URL_CHARS, download

_TODAY = dt.date(2026, 11, 5)
_FIXTURE = Path(__file__).parent / "fixtures" / "commons_imageinfo.json"
_BEACH = "Playa Bávaro"
_SKIPS = {"Playa Maimón": "coordinates still need confirming on a map"}
_TITLE = "File:Beach.jpg"
_THUMB = "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Beach.jpg/500px-Beach.jpg?utm_source=x"
_DOWNLOAD = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Beach.jpg/500px-Beach.jpg"
_PAGE_URL = "https://commons.wikimedia.org/wiki/File:Beach.jpg"


def _check(condition: bool, message: str) -> None:
    assert condition, message


_BEACH_CATEGORY = {"ns": 14, "title": "Category:Beaches of the Dominican Republic"}


def _page(**overrides: object) -> dict:
    """A page shaped like the real one in the fixture. None removes a field."""
    categories = overrides.pop("categories", [_BEACH_CATEGORY])
    pageid = overrides.pop("pageid", 1)
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
    return {"pageid": pageid, "ns": 6, "title": _TITLE, "index": 1, "imageinfo": [info], "categories": categories}


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
            return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": self.pages}})
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
    pages = [dict(_page(pageid=i + 1), title=f"File:{i}.jpg", index=20 - i) for i in range(15)]
    server = _Server(pages)
    report = candidates.build_report(_BEACH, commons.fetch_nearby(_api(server), 18.0, -68.0).pages, _TODAY)
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
        "prop": "imageinfo|categories", "clshow": "!hidden", "cllimit": "max",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "500",
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|DateTimeOriginal",
    }
    _check(params == expected, f"unexpected parameters: {params}")
    agent = server.requests[0].headers["User-Agent"]
    _check("github.com/Ayege/descubreplayas" in agent and "@" not in agent, "repo URL, no email")


@mock.patch.object(candidates, "SKIP_BEACHES", _SKIPS)
def test_markdown_escapes_untrusted_text() -> None:
    page = _page(Artist="[x](javascript:alert(1)) | `y`\n# z")
    report = candidates.build_report(_BEACH, [dict(page, title="File:a|b]<c>.jpg")], _TODAY)
    out = io.StringIO()
    candidates.format_markdown([report], ["Playa Maimón"], out)
    text = out.getvalue()
    _check("\\[x\\](javascript" in text and "\\|" in text, "the author can't make a link or a new cell")
    _check("a\\|b\\]" in text and "<c>" not in text, "the title is escaped")
    _check("Skipped: coordinates still need confirming on a map" in text, "a skipped beach is reported")


@mock.patch.object(candidates, "SKIP_BEACHES", _SKIPS)
def test_contact_sheet_is_inert_and_commands_are_quoted() -> None:
    nasty = "File:Bob's $(touch x) \"beach\" <b>.jpg"
    page = dict(_page(Artist='<b>Tom &amp; "Jerry"</b>&lt;script&gt;alert(1)&lt;/script&gt;'), title=nasty)
    report = candidates.build_report("Playa O'Hara", [page], _TODAY)
    out = io.StringIO()
    candidates.format_contact_sheet([report], ["Playa Maimón"], out)
    sheet = out.getvalue()
    _check(sheet.count("<script") == 1 and "onerror" not in sheet, "nothing live in the page except its own script")
    _check("<b>Tom" not in sheet and sheet.count("<script") == 1, "markup from Commons is never rendered")
    _check("Tom &amp; &quot;Jerry&quot;" in sheet, "the author is escaped")
    _check("Bob&#x27;s" in sheet and "&quot;beach&quot;" in sheet, "the title is escaped")
    _check("Content-Security-Policy" in sheet and "img-src https://upload.wikimedia.org" in sheet, "the page can load only thumbnails")
    sources = re.findall(r'<img src="([^"]*)"', sheet)
    _check(sources and all(src.startswith("https://upload.wikimedia.org/") for src in sources), "thumbnails only from our one host")
    raw_command = re.search(r"<code>(.*?)</code>", sheet).group(1)
    _check('"' not in raw_command and "<" not in raw_command, "the command is escaped inside the page")
    command = html.unescape(raw_command)
    words = shlex.split(command)
    _check(words[words.index("--commons-file") + 1] == report.candidates[0].title, "the title survives shell quoting")
    _check(words[words.index("--beach") + 1] == "Playa O'Hara", "so does the beach name")
    _check("$(touch x)" not in command.replace(shlex.quote(report.candidates[0].title), ""), "no expansion outside quotes")
    _check("Skipped" in sheet, "a skipped beach is listed")


class _Cards(HTMLParser):
    """Collects the checkboxes, inputs and any event-handler attributes of a sheet."""

    def __init__(self) -> None:
        super().__init__()
        self.picks: list[dict] = []
        self.alts: list[dict] = []
        self.handlers: list[str] = []
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        values = dict(attrs)
        self.handlers += [name for name in values if name.startswith("on")]
        if tag == "input" and "pick" in (values.get("class") or ""):
            self.picks.append(values)
        if tag == "input" and (values.get("class") or "").startswith("alt-"):
            self.alts.append(values)
        if tag == "a":
            self.hrefs.append(values.get("href") or "")


def _sheet(reports: list, skipped: tuple = ()) -> str:
    out = io.StringIO()
    candidates.format_contact_sheet(reports, list(skipped), out)
    return out.getvalue()


def test_sheet_script_is_the_only_thing_allowed_to_run() -> None:
    import base64
    import hashlib

    sheet = _sheet([candidates.build_report(_BEACH, [_page()], _TODAY)])
    scripts = re.findall(r"<script>(.*?)</script>", sheet, re.DOTALL)
    _check(scripts == [candidates._SHEET_SCRIPT], "exactly one script, the one we wrote")
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode("ascii")
    policy = html.unescape(re.search(r'Content-Security-Policy" content="([^"]*)"', sheet).group(1))
    _check(f"script-src 'sha256-{digest}'" in policy, "the CSP allows that script by its hash")
    _check("unsafe-inline" not in policy.split("script-src")[1].split(";")[0], "and nothing else may run")
    _check("default-src 'none'" in policy and "img-src https://upload.wikimedia.org" in policy and "form-action 'none'" in policy,
           "no other loads or form posts")
    parsed = _Cards()
    parsed.feed(sheet)
    _check(parsed.handlers == [], f"no inline event handlers: {parsed.handlers}")
    for forbidden in ("http", "XMLHttpRequest", "eval(", "innerHTML", "document.write", "location", "WebSocket", "sendBeacon"):
        _check(forbidden not in candidates._SHEET_SCRIPT, f"the script must not use {forbidden}")
    _check(candidates._SHEET_SCRIPT.count("fetch(") == 1 and 'fetch("submit", {' in candidates._SHEET_SCRIPT,
           "its one network call goes to the relative address submit")
    _check("connect-src 'self'" in policy, "and the page may only talk to its own server")


def test_sheet_picks_carry_exact_values_and_alt_limits() -> None:
    beach = "Playa O'Hara \"x\" <b>&"
    page = dict(_page(), title="File:Bob's \"beach\" & co.jpg")
    reports = [candidates.build_report(beach, [page, dict(page, title="File:Two.jpg", pageid=2)], _TODAY)]
    parsed = _Cards()
    parsed.feed(_sheet(reports))
    _check(len(parsed.picks) == 2 and len(parsed.alts) == 4, "a checkbox and two alt boxes per photo")
    _check(parsed.picks[0]["data-beach"] == beach and parsed.picks[0]["data-file"] == "File:Bob's \"beach\" & co.jpg",
           "the values the script reads are exact, whatever characters they hold")
    _check(all(alt["maxlength"] == str(candidates.MAX_ALT_CHARS) for alt in parsed.alts), "alt boxes stop at the length budget")
    _check(candidates.MAX_ALT_CHARS == 125, "which is 125")


def test_sheet_navigation_lists_only_beaches_with_photos() -> None:
    reports = [candidates.build_report("A", [_page()], _TODAY), candidates.build_report("B", [], _TODAY),
               candidates.build_report("C", [_page()], _TODAY)]
    sheet = _sheet(reports)
    parsed = _Cards()
    parsed.feed(sheet)
    anchors = [href for href in parsed.hrefs if href.startswith("#")]
    _check(anchors == ["#beach-0", "#beach-2"], f"navigation: {anchors}")
    _check(all(f'id="{a[1:]}"' in sheet for a in anchors), "every link has a target")


def test_placeholder_alt_text_is_refused_by_the_importer() -> None:
    words = shlex.split(candidates.import_command("Playa Bávaro", _TITLE))
    placeholders = {"es": words[words.index("--alt-es") + 1], "en": words[words.index("--alt-en") + 1]}
    for language in ("es", "en"):
        other = "en" if language == "es" else "es"
        extra = [f"--alt-{language}", placeholders[language], f"--alt-{other}", "Texto escrito por una persona"]
        with tempfile.TemporaryDirectory() as tmp:
            code, _out, err = _run_import(_Server(), Path(tmp), extra, alt=False)
            _check(code == 1 and "markup" in err, f"the {language} placeholder must not import: {err}")
            _check(not (Path(tmp) / "photos.json").exists(), "nothing written")


def test_radius_option() -> None:
    server = _Server()
    commons.fetch_nearby(_api(server), 18.0, -68.0, 10_000)
    _check(server.requests[0].url.params["ggsradius"] == "10000", "the radius is passed through")
    for bad in (9, 10_001, 0):
        try:
            commons.fetch_nearby(_api(_Server()), 18.0, -68.0, bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} should be refused")
    with tempfile.TemporaryDirectory() as tmp:
        for bad in ("9", "10001", "x"):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    candidates.main(["--beach", _BEACH, "--radius", bad, "--output", str(Path(tmp) / "c.md")])
            except SystemExit as exc:
                _check(exc.code == 2, "the parser refuses it before any request")
            else:
                raise AssertionError(f"--radius {bad} should be refused")
        server = _Server()
        sheet = Path(tmp) / "sheet.html"
        with contextlib.redirect_stdout(io.StringIO()):
            code = candidates.main(["--beach", _BEACH, "--radius", "5000", "--output", str(Path(tmp) / "c.md"),
                                    "--html", str(sheet)], server.transport, lambda _s: None)
        _check(code == 0 and sheet.exists() and server.requests[0].url.params["ggsradius"] == "5000", "html and radius work together")


# --- Categories -----------------------------------------------------------------------

def _cats(*names: str) -> list[dict]:
    return [{"ns": 14, "title": f"Category:{name}"} for name in names]


def _cat_page(page_id: int, *names: str, **fields: object) -> dict:
    return {"pageid": page_id, "ns": 6, "title": f"File:P{page_id}.jpg", "index": page_id,
            "categories": _cats(*names), **fields}


def _full_page(page_id: int, *names: str) -> dict:
    return dict(_page(pageid=page_id, categories=_cats(*names)), title=f"File:P{page_id}.jpg", index=page_id)


_NEXT = {"clcontinue": "7|Some_category", "continue": "||"}


def test_the_matching_words_are_whole_words_and_folded() -> None:
    tagged = ["Beaches of the Dominican Republic", "Playa Bávaro", "PLAYAS de Punta Cana", "Bavaro Beach", "playa"]
    for name in tagged:
        _check(match.classify([name]) == match.TAGGED, f"{name} should count")
    untagged = ["Beachfront hotels", "Praia de Punta Cana", "Seashore", "Grande", "Bonita", "Sosúa", "Cabarete",
                "Juan Dolio", "Punta Cana", "Rincón", "Beachy", "Playanos"]
    for name in untagged:
        _check(match.classify([name]) == match.NOT_TAGGED, f"{name}: {match.classify([name])}")
    _check(match.classify([]) == match.NOT_TAGGED, "no categories, no match")


def test_a_beach_name_counts_only_in_full() -> None:
    rules = match.beach_name_rules(BEACHES)
    cases = [
        ("Bahía de las Águilas", "Bahia de las Aguilas", True), ("Bahía de las Águilas", "Bahía", False),
        ("Bahía de las Águilas", "Águilas", False), ("Cayo Levantado", "Cayo Levantado (Samaná)", True),
        ("Cayo Levantado", "Cayo", False), ("Cayo Levantado", "Levantado", False), ("Punta Rucia", "Punta Rucia", True),
        ("Punta Rucia", "Punta Cana", False), ("La Ensenada", "Ensenada", False), ("Cabo Rojo", "Cabo Rojo", True),
        ("Cabo Rojo", "Rojo", False), ("Cayo Levantado", "Samaná", False),
    ]
    for beach, category, expected in cases:
        got = match.classify([category], rules[beach]) == match.TAGGED
        _check(got is expected, f"{beach} vs {category!r}: {got}")
    _check(match.classify(["Cayo Levantado"]) == match.NOT_TAGGED, "without the beach's rule, the name means nothing")


def test_shared_names_need_their_province() -> None:
    rules = match.beach_name_rules(BEACHES)
    _check(rules["Playa Blanca"].context == ("la", "altagracia"), "the first Playa Blanca needs its province")
    _check(rules["Playa Blanca (Pedernales)"].context == ("pedernales",), "the second needs Pedernales")
    _check(rules["Playa Grande"].context == (), "a unique name needs no context")
    synthetic = match.beach_name_rules([{"name": "Cayo X", "province": "Norte"}, {"name": "Cayo X (Sur)", "province": "Sur"}])
    north, south = synthetic["Cayo X"], synthetic["Cayo X (Sur)"]
    _check(match.classify(["Cayo X"], north) == match.NOT_TAGGED, "the bare shared name isn't enough")
    _check(match.classify(["Cayo X, Norte"], north) == match.TAGGED and match.classify(["Cayo X, Norte"], south) == match.NOT_TAGGED,
           "each beach takes its own province")
    _check(match.classify(["Cayo X (Sur)"], south) == match.TAGGED and match.classify(["Cayo X (Sur)"], north) == match.NOT_TAGGED,
           "and its own suffix")


def test_flag_terms_mark_mixed_and_never_drop() -> None:
    for term in ("Hotels in Bávaro", "Resort", "Swimming pools", "Restaurants", "Hotel rooms", "Markets", "Scuba diving", "Underwater"):
        _check(match.classify(["Playa Bávaro", term]) == match.MIXED, f"{term} should flag a tagged file")
        _check(match.classify([term]) == match.NOT_TAGGED, f"{term} alone doesn't tag anything")
    _check(match.classify(["Playa Bávaro", "Liverpool", "Bathroom"]) == match.TAGGED, "words only count from their start")
    _check(match.classify(["Beach resorts"]) == match.MIXED, "one category can do both")
    for constant in (match.BEACH_WORDS, match.FLAG_TERMS):
        _check(len(constant) >= 4, "named constants")


def test_continuation_merges_by_page_id_in_the_same_batch() -> None:
    server = _Server()
    first = {"query": {"pages": [dict(_page(pageid=1, categories=_cats("Cats A")), title="File:One.jpg", index=1),
                                 dict(_page(pageid=2, categories=[]), title="File:Two.jpg", index=2)]},
             "continue": {**_NEXT, "ggsoffset": "50"}}
    second = {"batchcomplete": True, "query": {"pages": [
        _cat_page(2, "Playa Bávaro", "Hotels", pageid=2), _cat_page(1, "Cats A", "Cats B")]}}
    server.api_responses = [httpx.Response(200, json=first), httpx.Response(200, json=second)]
    result = commons.fetch_nearby(_api(server), 18.0, -68.0)
    _check(result.categories_complete and len(server.requests) == 2, "two requests, then complete")
    sent = server.requests[1].url.params
    _check(sent["clcontinue"] == _NEXT["clcontinue"] and sent["continue"] == "||", "the continue token is sent back")
    _check("ggsoffset" not in sent and sent["ggscoord"] == "18.0|-68.0" and sent["ggslimit"] == "50", "same generator batch, never the next one")
    one, two = result.pages
    _check([c["title"] for c in one["categories"]] == ["Category:Cats A", "Category:Cats B"], "merged without duplicates")
    _check("imageinfo" in one and "imageinfo" in two, "the first response's imageinfo is kept")
    _check([c["title"] for c in two["categories"]] == ["Category:Playa Bávaro", "Category:Hotels"], "filled in by the later response")
    report = candidates.build_report(_BEACH, result.pages, _TODAY, result.categories_complete)
    _check(len(report.mixed) == 1 and report.dropped == 1,
           "page 1 has no beach category, and page 2 is mixed")


def test_incomplete_categories_are_unknown_not_dropped() -> None:
    page = _full_page(9, "Plants")
    tagged = _full_page(8, "Playa Bávaro")
    partial = httpx.Response(200, json={"query": {"pages": [page, tagged]}, "continue": _NEXT})
    server = _Server()
    server.api_responses = [partial] * 5
    result = commons.fetch_nearby(_api(server), 18.0, -68.0)
    _check(not result.categories_complete, "never completed")
    _check(len(server.requests) == 4, "the first request and at most 3 extra ones, as agreed")
    report = candidates.build_report(_BEACH, result.pages, _TODAY, result.categories_complete)
    _check([c.title for c in report.unknown] == ["File:P9.jpg"] and report.dropped == 0, "unknown, in its own group")
    _check([c.title for c in report.tagged] == ["File:P8.jpg"] and not report.mixed, "a match found so far still counts")
    complete = candidates.build_report(_BEACH, result.pages, _TODAY, True)
    _check(complete.dropped == 1 and not complete.unknown, "with complete categories the same file is dropped")
    out = io.StringIO()
    candidates.format_markdown([report], [], out)
    text = out.getvalue()
    _check("**Categories unknown**" in text and text.index("**Beach-tagged**") < text.index("**Categories unknown**"), "own section, after")
    sheet = io.StringIO()
    candidates.format_contact_sheet([report], [], sheet)
    _check("<h3>Categories unknown</h3>" in sheet.getvalue() and "<h3>Beach-tagged</h3>" in sheet.getvalue(), "and on the sheet")


def test_a_response_with_no_usable_continuation_is_incomplete() -> None:
    bad_tokens = [
        {"ggsoffset": "50", "continue": "-||"},
        {"clcontinue": ["x"], "continue": "||"},
        {"clcontinue": "x" * 500, "continue": "||"},
        {"clcontinue": "bad\nvalue", "continue": "||"},
        "text",
        None,
    ]
    for token in bad_tokens:
        server = _Server()
        body = {"query": {"pages": [_full_page(1, "Playa Bávaro")]}}
        if token is not None:
            body["continue"] = token
        server.api_responses = [httpx.Response(200, json=body)]
        result = commons.fetch_nearby(_api(server), 18.0, -68.0)
        _check(not result.categories_complete and len(server.requests) == 1, f"no follow-up for {token!r}")


def test_only_a_real_batchcomplete_counts() -> None:
    for value in ("yes", 1, "true", None, False):
        server = _Server()
        server.api_responses = [httpx.Response(200, json={"batchcomplete": value, "query": {"pages": [_full_page(1, "Playa X")]}})]
        _check(not commons.fetch_nearby(_api(server), 18.0, -68.0).categories_complete, f"{value!r} isn't batchcomplete")


def test_a_continued_response_never_overwrites_the_first() -> None:
    server = _Server()
    first = {"query": {"pages": [_full_page(1, "Cats A")]}, "continue": _NEXT}
    later = {"batchcomplete": True, "query": {"pages": [dict(_cat_page(1, "Cats B"), index=99, title="File:Other.jpg")]}}
    server.api_responses = [httpx.Response(200, json=first), httpx.Response(200, json=later)]
    (page,) = commons.fetch_nearby(_api(server), 18.0, -68.0).pages
    _check(page["index"] == 1 and page["title"] == "File:P1.jpg", "the first response's fields stay")
    _check(len(page["categories"]) == 2, "but the categories are added")


def test_a_name_rule_reaches_the_classification() -> None:
    rules = match.beach_name_rules(BEACHES)
    pages = [_full_page(1, "Cayo Levantado"), _full_page(2, "Plants")]
    with_rule = candidates.build_report("Cayo Levantado", pages, _TODAY, True, rules["Cayo Levantado"])
    _check(len(with_rule.tagged) == 1 and with_rule.dropped == 1, "the beach's full name tags a file")
    without = candidates.build_report("Cayo Levantado", pages, _TODAY, True)
    _check(not without.tagged and without.dropped == 2, "and without the rule it doesn't")
    server = _Server(pages)
    beach = {"name": "Cayo Levantado", "latitude": 19.2, "longitude": -69.2}
    reports = candidates.collect(_api(server), [beach], _TODAY, rules=rules)
    _check(len(reports[0].tagged) == 1, "collect passes each beach its own rule")
    _check(not candidates.collect(_api(_Server(pages)), [beach], _TODAY)[0].tagged, "and none without rules")


def test_review_order_is_tagged_then_mixed_then_unknown() -> None:
    report = candidates.build_report(_BEACH, [_full_page(1, "Playa X", "Hotels"), _full_page(2, "Playa X"), _full_page(3, "Plants")],
                                     _TODAY, False)
    _check([c.title for c in report.candidates] == ["File:P2.jpg", "File:P1.jpg", "File:P3.jpg"], "tagged, mixed, unknown")


def test_counts_line_and_summary_columns() -> None:
    pages = [_full_page(1, "Playa Bávaro"), _full_page(2, "Playa Bávaro", "Hotels"), _full_page(3, "Plants"),
             _full_page(4, "Playa Bávaro"), dict(_full_page(5, "Playa Bávaro"), imageinfo=[])]
    report = candidates.build_report(_BEACH, pages, _TODAY)
    _check(candidates.counts_line(report) == "Nearby 5 · beach-tagged 2 · mixed 1 · categories unknown 0 · dropped 1", candidates.counts_line(report))
    _check(report.refused["no imageinfo"] == 1, "other refusals are still counted")
    summary = candidates.format_summary([report])
    _check("near  tag mixed unkn drop other" in summary, summary)


def test_categories_are_cleaned() -> None:
    raw = [{"title": "Category:Playa <b>X</b>"}, {"title": "Hotels"}, {"title": 5}, "x", {"title": "Category:"}, None]
    _check(commons.parse_categories(raw) == ("Playa X", "Hotels"), commons.parse_categories(raw))
    _check(commons.parse_categories(None) == () and commons.parse_categories("x") == (), "not a list")
    many = [{"title": f"Category:C{i}"} for i in range(500)]
    _check(len(commons.parse_categories(many)) == commons.MAX_CATEGORIES_PER_FILE, "capped")


def test_import_does_not_ask_for_categories() -> None:
    server = _Server()
    _run_import(server, Path(tempfile.mkdtemp()), [])
    params = server.requests[0].url.params
    _check(params["prop"] == "imageinfo" and "clshow" not in params and "cllimit" not in params, "the import call is unchanged")


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
    pages = commons.fetch_nearby(_api(server, sleeps), 18.0, -68.0).pages
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


@mock.patch.object(candidates, "SKIP_BEACHES", _SKIPS)
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


# --- Manifest import ------------------------------------------------------------------

_BEACH_B = "Playa Macao"


def _manifest(root: Path, photos: list, name: str = "picks.json") -> Path:
    path = root / name
    path.write_text(json.dumps({"photos": photos}), encoding="utf-8")
    return path


def _entry(beach: str = _BEACH, **fields: object) -> dict:
    return {"beach": beach, "commons_file": _TITLE, "alt_es": "Palmeras y arena", "alt_en": "Palm trees and sand", **fields}


def _run_manifest(server: _Server, root: Path, path: Path, extra: list[str] | None = None) -> tuple[int, str, str]:
    args = ["--manifest", str(path), "--output", str(root / "photos.json"), "--static-dir", str(root / "static"), *(extra or [])]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = importer.main(args, server.transport, lambda _s: None)
    return code, out.getvalue(), err.getvalue()


def test_manifest_imports_every_entry_and_prints_one_checklist() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        server = _Server()
        code, out, _err = _run_manifest(server, root, _manifest(root, [_entry(), _entry(_BEACH_B)]))
        _check(code == 0 and "2 of 2 imported." in out, out)
        photos = json.loads((root / "photos.json").read_text(encoding="utf-8"))
        _check(sorted(photos) == sorted([_BEACH, _BEACH_B]) and all(beach_photo_problems(p) == [] for p in photos.values()), "valid entries")
        _check(photos[_BEACH]["alt"]["en"] == "Palm trees and sand", "the alt text is the one given")
        _check(out.count("no identifiable person, no child") == 1, "the checklist is printed once")
        _check(server.hosts() == {"commons.wikimedia.org", "upload.wikimedia.org"}, "the same two hosts as a single import")


def test_manifest_is_checked_whole_before_any_request() -> None:
    bad_cases = {
        "not json": "{oops",
        "not an object": "[]",
        "extra top-level key": json.dumps({"photos": [_entry()], "x": 1}),
        "empty": json.dumps({"photos": []}),
        "too many": json.dumps({"photos": [_entry()] * (importer.MAX_MANIFEST_PHOTOS + 1)}),
        "unknown beach": json.dumps({"photos": [_entry("Playa Inventada")]}),
        "duplicate beach": json.dumps({"photos": [_entry(), _entry()]}),
        "missing alt": json.dumps({"photos": [_entry(alt_es="  ")]}),
        "markup in alt": json.dumps({"photos": [_entry(alt_en="<describe the scene>")]}),
        "alt too long": json.dumps({"photos": [_entry(alt_es="a" * 126)]}),
        "unknown key": json.dumps({"photos": [_entry(note="x")]}),
        "both origins": json.dumps({"photos": [_entry(own="x.jpg", year=2024)]}),
        "no origin": json.dumps({"photos": [{k: v for k, v in _entry().items() if k != "commons_file"}]}),
        "own without year": json.dumps({"photos": [_entry(commons_file=None, own="x.jpg")]}),
        "year with commons": json.dumps({"photos": [_entry(year=2020)]}),
        "bad title": json.dumps({"photos": [_entry(commons_file="File:A.jpg|File:B.jpg")]}),
        "entry not an object": json.dumps({"photos": ["x"]}),
        "good entry then a bad one": json.dumps({"photos": [_entry(), _entry(_BEACH_B, alt_en="")]}),
    }
    for name, text in bad_cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "picks.json"
            path.write_text(text, encoding="utf-8")
            server = _Server()
            code, _out, err = _run_manifest(server, root, path)
            _check(code == 1 and "Refused, nothing written" in err, f"{name}: {err}")
            _check(not server.requests and not (root / "photos.json").exists(), f"{name}: nothing fetched or written")
    with tempfile.TemporaryDirectory() as tmp:
        code, _out, err = _run_manifest(_Server(), Path(tmp), Path(tmp) / "missing.json")
        _check(code == 1 and "can't be read" in err, "a missing file is refused")


def test_manifest_replace_rules() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _run_manifest(_Server(), root, _manifest(root, [_entry()]))
        before = (root / "photos.json").read_bytes()
        server = _Server(image=_jpeg((500, 300)))
        code, _out, err = _run_manifest(server, root, _manifest(root, [_entry(_BEACH_B), _entry()], "again.json"))
        _check(code == 1 and "already have a photo: Playa Bávaro" in err and "--replace" in err, err)
        _check(not server.requests and (root / "photos.json").read_bytes() == before, "all or nothing, before any request")
        code, _out, _err = _run_manifest(server, root, _manifest(root, [_entry(_BEACH_B), _entry()], "again.json"), ["--replace"])
        _check(code == 0 and len(json.loads((root / "photos.json").read_text(encoding="utf-8"))) == 2, "with --replace it goes ahead")


def test_manifest_one_refusal_does_not_stop_the_others() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _manifest(root, [_entry(), _entry(_BEACH_B, commons_file="File:Other.jpg"), _entry("Playa Juanillo")])
        code, out, err = _run_manifest(_Server(), root, path)
        photos = json.loads((root / "photos.json").read_text(encoding="utf-8"))
        _check(code == 1 and sorted(photos) == sorted([_BEACH, "Playa Juanillo"]), "the other two are imported")
        _check("FAILED Playa Macao" in err and "2 of 3 imported." in out, f"{err} {out}")


def test_manifest_stops_after_a_429() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        server = _Server()
        server.image_response = httpx.Response(429)
        code, out, err = _run_manifest(server, root, _manifest(root, [_entry(), _entry(_BEACH_B), _entry("Playa Juanillo")]))
        _check(code == 1 and "0 of 3 imported." in out, out)
        _check("no identifiable person" not in out, "no checklist when nothing was imported")
        _check(err.count("not attempted: stopped after HTTP 429") == 2 and "HTTP 429" in err, err)
        _check(sum(r.url.host == "upload.wikimedia.org" for r in server.requests) == 1, "one download attempt, no loop")
        _check(sum(r.url.host == "commons.wikimedia.org" for r in server.requests) == 1, "and the rest never start")
        _check(not (root / "photos.json").exists() or json.loads((root / "photos.json").read_text(encoding="utf-8")) == {}, "nothing written")


def test_manifest_limits_and_messages() -> None:
    _check(importer.MAX_MANIFEST_PHOTOS == 100 and importer.MAX_MANIFEST_BYTES == 1024 * 1024, "the agreed limits")
    names = [beach["name"] for beach in BEACHES[:4]]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        many = _manifest(root, [_entry(name) for name in names])
        with mock.patch.object(importer, "MAX_MANIFEST_PHOTOS", 3):
            code, _out, err = _run_manifest(_Server(), root, many)
        _check(code == 1 and "entries" in err and not (root / "photos.json").exists(), f"too many distinct photos: {err}")
        empty = _manifest(root, [], "empty.json")
        code, _out, err = _run_manifest(_Server(), root, empty)
        _check(code == 1 and "entries" in err, f"an empty list: {err}")
        big = root / "big.json"
        big.write_text(json.dumps({"photos": [_entry()], "pad": "x" * 2000}), encoding="utf-8")
        with mock.patch.object(importer, "MAX_MANIFEST_BYTES", 1000):
            code, _out, err = _run_manifest(_Server(), root, big)
        _check(code == 1 and "over 1000 bytes" in err, f"an oversized file: {err}")
        cases = {
            "both origins": (_entry(own="x.jpg", year=2024), "exactly one of commons_file or own"),
            "no origin": ({k: v for k, v in _entry().items() if k != "commons_file"}, "exactly one of commons_file or own"),
            "year is true": (_entry(commons_file=None, own="x.jpg", year=True), "whole-number year"),
            "year is text": (_entry(commons_file=None, own="x.jpg", year="2024"), "whole-number year"),
        }
        for name, (entry, message) in cases.items():
            code, _out, err = _run_manifest(_Server(), root, _manifest(root, [entry], "case.json"))
            _check(code == 1 and message in err, f"{name}: {err}")


def test_run_manifest_does_not_replace_unless_told() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _run_manifest(_Server(), root, _manifest(root, [_entry()]))
        before = (root / "photos.json").read_bytes()
        args = argparse.Namespace(replace=False, output=root / "photos.json", static_dir=root / "static")
        entries = [importer._manifest_entry(_entry(), 1, args, _TODAY)]
        outcomes = importer.run_manifest(entries, _api(_Server(image=_jpeg((500, 300)))), _TODAY)
        _check(outcomes[0].record is None and "--replace" in outcomes[0].error, "the importer itself refuses too")
        _check((root / "photos.json").read_bytes() == before, "and writes nothing")


def test_manifest_accepts_own_photos() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        photo = root / "mine.jpg"
        photo.write_bytes(_jpeg((1200, 800)))
        server = _Server()
        path = _manifest(root, [{"beach": _BEACH_B, "own": str(photo), "year": 2024, "alt_es": "Mar", "alt_en": "Sea"}])
        code, _out, _err = _run_manifest(server, root, path)
        entry = json.loads((root / "photos.json").read_text(encoding="utf-8"))[_BEACH_B]
        _check(code == 0 and not server.requests and entry["source"] == "own" and entry["taken_year"] == 2024, "own photo, no network")


def test_manifest_cannot_be_mixed_with_single_photo_options() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _manifest(root, [_entry()])
        for extra in (["--beach", _BEACH], ["--commons-file", _TITLE], ["--alt-es", "x"], ["--own", "x.jpg"], ["--year", "2020"]):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    _run_manifest(_Server(), root, path, extra)
            except SystemExit as exc:
                _check(exc.code == 2, "refused by the parser")
            else:
                raise AssertionError(f"{extra} should be refused with --manifest")
        for args in ([], ["--beach", _BEACH], ["--beach", _BEACH, "--alt-es", "a", "--alt-en", "b"]):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    importer.main(args)
            except SystemExit as exc:
                _check(exc.code == 2, f"{args} is refused")
            else:
                raise AssertionError(f"{args} should be refused")


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
