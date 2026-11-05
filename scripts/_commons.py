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

"""Read Wikimedia Commons file metadata and decide what we may use.

Everything from the API is untrusted. Licences are an exact allowlist, and both of the
fields that name a licence have to agree. Thumbnails come only in Wikimedia's standard
widths. One request at a time, using maxlag, and an HTTP 429 is never retried.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import urlsplit

from dashboard.conoce_data import (
    MAX_ATTRIBUTION_CHARS,
    MAX_BEACH_PHOTO_WIDTH,
    MIN_PHOTO_YEAR,
    is_safe_url,
)
from scripts._http import (
    HTTP_TOO_MANY_REQUESTS,
    RETRY_STATUSES,
    Api,
    FetchError,
    get_json_with_headers,
    retry_wait,
)
from scripts._images import MAX_DOWNLOAD_URL_CHARS
from scripts.fetch_species import clean_text

API_URL = "https://commons.wikimedia.org/w/api.php"
SOURCE_HOST = "commons.wikimedia.org"
# The only host we download from. The API currently names thumb.wikimedia.org in
# thumburl, so thumbnail_download_url() moves the validated path onto this host.
DOWNLOAD_HOST = "upload.wikimedia.org"
DOWNLOAD_HOSTS = frozenset({DOWNLOAD_HOST})
_THUMBNAIL_URL_HOSTS = frozenset({DOWNLOAD_HOST, "thumb.wikimedia.org"})

# Wikimedia serves only these widths: https://w.wiki/GHai
# https://www.mediawiki.org/wiki/Common_thumbnail_sizes
STANDARD_THUMB_WIDTHS = frozenset({20, 40, 60, 120, 250, 330, 500, 960, 1280, 1920, 3840})
# We download this width; _images.py stores it at MAX_BEACH_PHOTO_WIDTH (480 px).
DOWNLOAD_WIDTH = 500

SEARCH_RADIUS_M = 2000
# The geosearch limits in the API's own help: 10 to 10,000 m.
MIN_SEARCH_RADIUS_M = 10
MAX_SEARCH_RADIUS_M = 10_000
SEARCH_LIMIT = 50
MIN_ORIGINAL_WIDTH = 800
ALLOWED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
REQUEST_DELAY_S = 1.0
MAXLAG_S = 5
MAXLAG_ATTEMPTS = 3
# Extra requests for one beach when its categories don't fit in one response.
MAX_CATEGORY_CONTINUATIONS = 3
MAX_CATEGORIES_PER_FILE = 100
MAX_CONTINUE_VALUE_CHARS = 300
CATEGORY_PREFIX = "Category:"
MAXLAG_ERROR = "maxlag"
FILE_NAMESPACE = 6
MAX_TITLE_CHARS = 255
# A 429 can mean a wrong thumbnail size as well as a rate limit, so waiting won't help.
RETRY_STATUSES_NO_429 = RETRY_STATUSES - {HTTP_TOO_MANY_REQUESTS}

_METADATA_FIELDS = "LicenseShortName|LicenseUrl|Artist|DateTimeOriginal"
_BASE_PARAMS: dict[str, object] = {
    "action": "query",
    "format": "json",
    "formatversion": 2,
    "maxlag": MAXLAG_S,
    "prop": "imageinfo",
    "iiprop": "url|size|mime|extmetadata",
    "iiurlwidth": DOWNLOAD_WIDTH,
    "iiextmetadatafilter": _METADATA_FIELDS,
}

# The search also asks for each file's visible categories. cllimit=max is the API's
# limit for the whole batch, so a response without batchcomplete is partial.
_SEARCH_PARAMS = {**_BASE_PARAMS, "prop": "imageinfo|categories", "clshow": "!hidden", "cllimit": "max"}

_CC_VERSIONS = r"1\.0|2\.0|2\.5|3\.0|4\.0"
_LICENSE_NAME_RE = re.compile(rf"CC0(?: 1\.0)?|CC (BY|BY-SA) ({_CC_VERSIONS})")
_LICENSE_URL_RE = re.compile(
    rf"https?://creativecommons\.org/(?:licenses/(by|by-sa)/({_CC_VERSIONS})|publicdomain/zero/1\.0)"
    r"(?:/(?:(?:deed|legalcode)(?:\.[A-Za-z-]+)?)?)?"
)
_CC0_KEY = ("cc0", "1.0")
_THUMB_PATH_RE = re.compile(r"/wikipedia/commons/thumb/[0-9a-f]/[0-9a-f]{2}/(?!\.\.?/)[^/]+/([0-9]+)px-[^/]+")
_YEAR_RE = re.compile(r"(?<!\w)[0-9]{4}(?!\w)")
_UNCERTAIN_DATE_RE = re.compile(
    r"\b(?:circa|ca|c|around|about|before|after|between|unknown|possibly|probably|antes|después|hacia)\b|[?~]",
    re.IGNORECASE,
)
_FILE_TITLE_RE = re.compile(r"File:[^|#<>\[\]{}\x00-\x1f\x7f]+")

REASON_NO_INFO = "no imageinfo"
REASON_MIME = "file type"
REASON_TOO_NARROW = "under 800 px wide"
REASON_LICENSE = "licence not allowed"
REASON_NO_AUTHOR = "no usable author"
REASON_BAD_SOURCE = "bad file page URL"
REASON_NO_THUMBNAIL = "no usable thumbnail URL"
REASON_NON_STANDARD_WIDTH = "non-standard thumbnail width"


class License(NamedTuple):
    """The licence we use for a file, of the options it may have."""

    code: str
    label: str
    url: str


class LicenseDecision(NamedTuple):
    """A usable licence, or else whether a person should look at the file."""

    license: License | None
    needs_manual_check: bool


class Candidate(NamedTuple):
    """A file that passed every automatic check. A person still has to review it."""

    title: str
    source_url: str
    thumbnail_url: str
    width: int
    height: int
    license: License
    author: str
    year: int | None
    categories: tuple[str, ...] = ()


class ManualCheck(NamedTuple):
    """A file whose two licence fields disagree, so it is refused."""

    title: str
    source_url: str
    license_name: str
    license_url: str


class Refusal(NamedTuple):
    """Why a file can't be used."""

    reason: str


class ThumbnailRefused(ValueError):
    """A thumbnail URL didn't pass, with the reason to report."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _TextOnly(HTMLParser):
    """Collects text, with entities decoded and scripts and styles dropped."""

    _DROPPED = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._dropping = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self._dropping += tag in self._DROPPED

    def handle_endtag(self, tag: str) -> None:
        if tag in self._DROPPED and self._dropping:
            self._dropping -= 1

    def handle_data(self, data: str) -> None:
        if not self._dropping:
            self.parts.append(data)


def strip_html(value: object) -> str:
    """The text of an HTML fragment. Returns "" for anything that isn't a string."""
    if not isinstance(value, str):
        return ""
    parser = _TextOnly()
    parser.feed(value)
    parser.close()
    return " ".join(parser.parts)


def parse_author(value: object) -> str | None:
    """Plain-text author. Too long gives None: a cut-off name would misattribute."""
    text = clean_text(strip_html(value), MAX_ATTRIBUTION_CHARS + 1)
    return None if text is None or len(text) > MAX_ATTRIBUTION_CHARS else text


def parse_year(value: object, today: dt.date) -> int | None:
    """A year only if the text gives exactly one and doesn't hedge; never a guess."""
    text = strip_html(value)
    if _UNCERTAIN_DATE_RE.search(text):
        return None
    years = set(_YEAR_RE.findall(text))
    if len(years) != 1:
        return None
    year = int(years.pop())
    return year if MIN_PHOTO_YEAR <= year <= today.year else None


def _name_key(name: object) -> tuple[str, str] | None:
    match = _LICENSE_NAME_RE.fullmatch(name) if isinstance(name, str) else None
    if match is None:
        return None
    return (f"cc-{match.group(1).lower()}", match.group(2)) if match.group(1) else _CC0_KEY


def _url_key(url: object) -> tuple[str, str] | None:
    match = _LICENSE_URL_RE.fullmatch(url) if isinstance(url, str) else None
    if match is None:
        return None
    return (f"cc-{match.group(1)}", match.group(2)) if match.group(1) else _CC0_KEY


def _license_from_key(key: tuple[str, str]) -> License:
    code, version = key
    if key == _CC0_KEY:
        return License(code, "CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/")
    slug = code.removeprefix("cc-")
    return License(code, f"CC {slug.upper()} {version}", f"https://creativecommons.org/licenses/{slug}/{version}/")


def decide_license(name: object, url: object) -> LicenseDecision:
    """Allow a licence only when the name and the URL name the same one and version.

    The metadata has mixed licences up before, such as a CC BY-SA photo of a
    public-domain object reported as "Public domain". So if only one field names an
    allowed licence, a person has to check it. If neither does, it's simply refused.
    """
    name_key, url_key = _name_key(name), _url_key(url)
    if name_key is not None and name_key == url_key:
        return LicenseDecision(_license_from_key(name_key), needs_manual_check=False)
    return LicenseDecision(None, needs_manual_check=name_key is not None or url_key is not None)


def thumbnail_download_url(thumb_url: object, thumb_width: object) -> str:
    """Check the API's thumbnail URL and return it on our one download host."""
    if not is_safe_url(thumb_url, MAX_DOWNLOAD_URL_CHARS):
        raise ThumbnailRefused(REASON_NO_THUMBNAIL)
    parts = urlsplit(thumb_url)
    match = _THUMB_PATH_RE.fullmatch(parts.path)
    if parts.hostname not in _THUMBNAIL_URL_HOSTS or parts.port is not None or match is None:
        raise ThumbnailRefused(REASON_NO_THUMBNAIL)
    width = int(match.group(1))
    if width not in STANDARD_THUMB_WIDTHS or width < MAX_BEACH_PHOTO_WIDTH or thumb_width != width:
        raise ThumbnailRefused(REASON_NON_STANDARD_WIDTH)
    return f"https://{DOWNLOAD_HOST}{parts.path}"


def _source_url(url: object) -> str | None:
    if not is_safe_url(url):
        return None
    parts = urlsplit(url)
    if parts.hostname != SOURCE_HOST or parts.port is not None or not parts.path.startswith("/wiki/File:"):
        return None
    return f"https://{SOURCE_HOST}{parts.path}"


def _metadata_value(metadata: Mapping, field: str) -> object:
    entry = metadata.get(field)
    return entry.get("value") if isinstance(entry, Mapping) else None


def _dimension(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def parse_categories(value: object) -> tuple[str, ...]:
    """Category names without the "Category:" prefix, as plain text."""
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for entry in value[:MAX_CATEGORIES_PER_FILE]:
        title = entry.get("title") if isinstance(entry, Mapping) else None
        name = clean_text(title.removeprefix(CATEGORY_PREFIX) if isinstance(title, str) else None, MAX_TITLE_CHARS)
        if name:
            names.append(name)
    return tuple(names)


def parse_page(page: object, today: dt.date) -> Candidate | ManualCheck | Refusal:
    """Apply every automatic check to one page of an API response."""
    info = page.get("imageinfo") if isinstance(page, Mapping) else None
    title = page.get("title") if isinstance(page, Mapping) else None
    title = clean_text(title, MAX_TITLE_CHARS)
    if not title or not isinstance(info, list) or not info or not isinstance(info[0], Mapping):
        return Refusal(REASON_NO_INFO)
    info = info[0]
    width, height = _dimension(info.get("width")), _dimension(info.get("height"))
    if info.get("mime") not in ALLOWED_MIME_TYPES:
        return Refusal(REASON_MIME)
    if width < MIN_ORIGINAL_WIDTH or not height:
        return Refusal(REASON_TOO_NARROW)
    source_url = _source_url(info.get("descriptionurl"))
    if source_url is None:
        return Refusal(REASON_BAD_SOURCE)
    metadata = info.get("extmetadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    name, url = _metadata_value(metadata, "LicenseShortName"), _metadata_value(metadata, "LicenseUrl")
    decision = decide_license(name, url)
    if decision.license is None:
        if decision.needs_manual_check:
            return ManualCheck(title, source_url, clean_text(name, 60) or "", clean_text(url, 120) or "")
        return Refusal(REASON_LICENSE)
    author = parse_author(_metadata_value(metadata, "Artist"))
    if author is None:
        return Refusal(REASON_NO_AUTHOR)
    try:
        thumbnail = thumbnail_download_url(info.get("thumburl"), info.get("thumbwidth"))
    except ThumbnailRefused as exc:
        return Refusal(exc.reason)
    year = parse_year(_metadata_value(metadata, "DateTimeOriginal"), today)
    categories = parse_categories(page.get("categories"))
    return Candidate(title, source_url, thumbnail, width, height, decision.license, author, year, categories)


def _query(api: Api, params: Mapping[str, object]) -> dict:
    """One API query. maxlag errors arrive as a normal response, with Retry-After."""
    for attempt in range(MAXLAG_ATTEMPTS):
        payload, headers = get_json_with_headers(api, API_URL, params, REQUEST_DELAY_S, RETRY_STATUSES_NO_429)
        error = payload.get("error")
        if error is None:
            return payload
        code = error.get("code") if isinstance(error, Mapping) else None
        if code != MAXLAG_ERROR:
            raise FetchError(f"Commons API error: {clean_text(code, 60)}")
        api.sleep(retry_wait(headers, attempt))
    raise FetchError(f"Commons still lagging after {MAXLAG_ATTEMPTS} attempts")


def _pages(payload: Mapping) -> list:
    query = payload.get("query")
    pages = query.get("pages") if isinstance(query, Mapping) else None
    return pages if isinstance(pages, list) else []


def _distance_rank(page: object) -> int:
    index = page.get("index") if isinstance(page, Mapping) else None
    return index if isinstance(index, int) else SEARCH_LIMIT + 1


class NearbyResult(NamedTuple):
    """The pages near a point, closest first, and whether their categories are whole."""

    pages: list
    categories_complete: bool


def _continuation(payload: Mapping) -> dict[str, str] | None:
    """The values to send back to keep the same batch, or None if there is no usable
    clcontinue. Anything that would move to the next generator batch is ignored.
    """
    token = payload.get("continue")
    if not isinstance(token, Mapping):
        return None
    cleaned: dict[str, str] = {}
    for key in ("continue", "clcontinue"):
        value = token.get(key)
        if isinstance(value, str) and len(value) <= MAX_CONTINUE_VALUE_CHARS and value.isprintable():
            cleaned[key] = value
    return cleaned if "clcontinue" in cleaned else None


def _merge_page(merged: dict, page: object) -> None:
    """Fold a page from a continued response into what we have, by page id. A
    continued response repeats a page with only some of its categories.
    """
    if not isinstance(page, Mapping):
        return
    key = page.get("pageid", page.get("title"))
    if key is None:
        return
    have = merged.setdefault(key, {})
    for field, value in page.items():
        if field != "categories":
            have.setdefault(field, value)
            continue
        known = have.setdefault("categories", [])
        seen = {entry.get("title") for entry in known}
        for entry in value if isinstance(value, list) else []:
            if isinstance(entry, Mapping) and entry.get("title") not in seen:
                known.append(entry)
                seen.add(entry.get("title"))


def fetch_nearby(api: Api, lat: float, lon: float, radius_m: int = SEARCH_RADIUS_M) -> NearbyResult:
    """File pages near a point with their categories, closest first. An empty result
    is a normal answer. If the categories don't fit in one response, up to
    MAX_CATEGORY_CONTINUATIONS more requests fill them in; if the batch is still not
    complete after that, categories_complete is False.
    """
    if not MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M:
        raise ValueError(f"radius must be {MIN_SEARCH_RADIUS_M} to {MAX_SEARCH_RADIUS_M} m")
    params: dict[str, object] = {
        **_SEARCH_PARAMS,
        "generator": "geosearch",
        "ggscoord": f"{lat}|{lon}",
        "ggsradius": radius_m,
        "ggslimit": SEARCH_LIMIT,
        "ggsnamespace": FILE_NAMESPACE,
    }
    merged: dict = {}
    for _attempt in range(MAX_CATEGORY_CONTINUATIONS + 1):
        payload = _query(api, params)
        for page in _pages(payload):
            _merge_page(merged, page)
        if payload.get("batchcomplete") is True:
            return NearbyResult(sorted(merged.values(), key=_distance_rank), True)
        follow = _continuation(payload)
        if follow is None:
            break
        params = {**params, **follow}
    return NearbyResult(sorted(merged.values(), key=_distance_rank), False)


def normalise_title(title: str) -> str | None:
    """The title in File:Name form, or None. Refusing "|" stops a second title
    being slipped into the query.
    """
    text = unicodedata.normalize("NFC", title.replace("_", " ").strip())
    if len(text) > MAX_TITLE_CHARS or _FILE_TITLE_RE.fullmatch(text) is None:
        return None
    name = text.removeprefix("File:").strip()
    return f"File:{name[:1].upper()}{name[1:]}" if name else None


def fetch_file(api: Api, title: str) -> Candidate | ManualCheck | Refusal:
    """Fetch one file fresh. Nothing from an earlier candidates list is trusted."""
    pages = _pages(_query(api, {**_BASE_PARAMS, "titles": title}))
    if len(pages) != 1 or not isinstance(pages[0], Mapping):
        return Refusal(REASON_NO_INFO)
    page = pages[0]
    if page.get("missing") or page.get("ns") != FILE_NAMESPACE or page.get("title") != title:
        return Refusal(REASON_NO_INFO)
    return parse_page(page, dt.date.today())
