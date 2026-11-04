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

"""Logic layer for "Conoce esta playa": data shapes, validation and indexing.

Pure functions only: no Streamlit, no network. The single disk read is the
one-time load behind load_index(). Records that fail validation are dropped and
logged at load time, so one bad fact can never break a beach's panel, and
tests/test_profiles.py fails if a bad record is ever committed.

This module deliberately omits ``from __future__ import annotations``: with it,
Python 3.11 cannot see NotRequired, so TypedDict.__required_keys__ would be
wrong, and the closed-shape checks below rely on those key sets.
"""
import datetime as dt
import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import NotRequired, TypedDict
from urllib.parse import urlencode, urlsplit

from dashboard.beach_profiles import COASTAL_UNITS, MARINE_OVERRIDES, PROFILES
from dashboard.beaches_data import BEACHES

logger = logging.getLogger(__name__)

AUTO_SPECIES_PATH = Path(__file__).with_name("data") / "species_auto.json"
BEACH_PHOTOS_PATH = Path(__file__).with_name("data") / "beach_photos.json"
STATIC_DIR = Path(__file__).with_name("static")
AUTO_SCHEMA_VERSION = 1
MAX_AUTO_SPECIES_BYTES = 300 * 1024

LANGUAGES = ("es", "en")
DEFAULT_LANGUAGE = "es"

# Size budgets, in characters.
MAX_TEASER_CHARS = 90
MAX_BLURB_CHARS = 140
MAX_SARGASSUM_NOTE_CHARS = 140
MAX_ROCA_TITLE_CHARS = 80
MAX_GEOLOGY_CHARS = 400
MAX_ROCK_TYPE_CHARS = 80
MAX_SAND_CHARS = 140
MAX_SARGAZO_WHY_CHARS = 200
MAX_HISTORY_BODY_CHARS = 500
MAX_HISTORY_TITLE_CHARS = 80
MAX_NAME_CHARS = 80
MAX_ICONIC_GROUP_CHARS = 40
MAX_ATTRIBUTION_CHARS = 120
MAX_SOURCE_LABEL_CHARS = 120
MAX_URL_CHARS = 300

# Image budgets.
MAX_BEACH_PHOTO_WIDTH = 480
MAX_BEACH_PHOTO_BYTES = 45 * 1024
THUMBNAIL_FILE_PX = 96
MAX_THUMBNAIL_BYTES = 6 * 1024
MAX_STATIC_BYTES = 3 * 1024 * 1024
MAX_ALT_CHARS = 125
MIN_PHOTO_YEAR = 1900

# Ranking.
MAX_SAME_GROUP_IN_TOP = 2
MAX_PLANTS_IN_TOP = 1
PLANT_GROUP = "Plantae"
TIER_MARINE_WITH_PHOTO = 0
TIER_ANY_PHOTO = 1
TIER_NO_PHOTO = 2

# Count budgets.
MAX_FEATURED_SPECIES = 3
MAX_MORE_SPECIES = 5
MAX_SPECIES_TOTAL = MAX_FEATURED_SPECIES + MAX_MORE_SPECIES
MAX_HISTORY_ITEMS = 3
MAX_SOURCES_PER_FACT = 3

METHOD_CURATED = "curated"
METHOD_LLM_REVIEWED = "llm_draft_reviewed"
METHOD_INATURALIST = "api:inaturalist"
METHOD_MACROSTRAT = "api:macrostrat"
AUTO_SPECIES_METHODS = frozenset({METHOD_INATURALIST})
HANDWRITTEN_METHODS = frozenset({METHOD_CURATED, METHOD_LLM_REVIEWED})
ROCA_METHODS = HANDWRITTEN_METHODS | {METHOD_MACROSTRAT}
ALLOWED_METHODS = AUTO_SPECIES_METHODS | ROCA_METHODS

# Display label and deed URL per license. An allowlist, so NC, ND and "all
# rights reserved" are refused by not being here.
PHOTO_LICENSES = MappingProxyType({
    "cc0": ("CC0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "cc-by": ("CC BY", "https://creativecommons.org/licenses/by/4.0/"),
    "cc-by-sa": ("CC BY-SA", "https://creativecommons.org/licenses/by-sa/4.0/"),
})

# iNaturalist's open-data bucket. static.inaturalist.org is left out on purpose:
# it hosts the photos that are NOT openly licensed. The bucket still contains
# CC-BY-NC photos, so PHOTO_LICENSES is checked as well as the host.
# https://github.com/inaturalist/inaturalist-open-data
IMAGE_HOSTS = frozenset({"inaturalist-open-data.s3.amazonaws.com"})
HTTPS_SCHEME = "https"
HTTPS_PORT = 443
# "square" is the smallest size the bucket offers (exactly 75x75 px).
IMAGE_PATH_RE = re.compile(r"/photos/\d+/square\.(?:jpe?g|png)", re.IGNORECASE)
# Image paths are content-addressed, so they never contain data from a source.
IMAGE_NAME_RE = re.compile(r"[0-9a-f]{64}\.webp")
BEACH_IMAGE_FILE_RE = re.compile(r"beaches/([0-9a-f]{64})\.webp")
SPECIES_IMAGE_FILE_RE = re.compile(r"species/([0-9a-f]{64})\.webp")
BEACH_PHOTO_SOURCES = frozenset({"own", "wikimedia_commons"})
_URL_FORBIDDEN_CHARS = frozenset(' \\<>"')

# Data stays plain text: no markup, control characters or bidirectional overrides.
_FORBIDDEN_TEXT_RE = re.compile(r"[<>\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")

INATURALIST_LABEL = "iNaturalist"
INATURALIST_OBSERVATIONS_URL = "https://www.inaturalist.org/observations"
DEFAULT_RADIUS_KM = 2


class Source(TypedDict):
    """A citation. The label stays in its original language."""

    label: str
    url: str


class Localized(TypedDict):
    """Prose in both languages. Spanish is authored first."""

    es: str
    en: str


class OptionalNames(TypedDict):
    """Common names from the API, which may have none in a language."""

    es: str | None
    en: str | None


class Sourced(TypedDict):
    """How we know a fact. verified_at is required for llm_draft_reviewed."""

    method: str
    sources: list[Source]
    verified_at: NotRequired[str]


class Photo(TypedDict):
    """An openly licensed reference photo of the species, not of the beach.

    file, width, height and sha256 describe the self-hosted copy. They are set
    together or not at all, until the ingest always downloads a copy.
    """

    url: str
    license_code: str
    attribution: str
    source_url: str
    origin: str
    file: NotRequired[str]
    width: NotRequired[int]
    height: NotRequired[int]
    sha256: NotRequired[str]


class BeachPhoto(TypedDict):
    """One reference photo of a beach, self-hosted and reviewed by a person."""

    file: str
    width: int
    height: int
    bytes: int
    sha256: str
    author: str
    license: str
    license_url: str
    source_url: str
    source: str
    taken_year: int | None
    alt: Localized
    method: str
    reviewed_at: str


class AutoTaxon(TypedDict):
    """One species in species_auto.json, shared by every beach that lists it.

    The closed shape keeps coordinates out. marine is the tag as OBIS gave it;
    MARINE_OVERRIDES corrects it at load time.
    """

    taxon_id: int
    scientific_name: str
    common_name: OptionalNames
    iconic_group: str | None
    marine: bool
    marine_source: str | None
    marine_tagged_at: str | None
    photo: Photo | None
    method: str
    fetched_at: str


class BeachTaxon(TypedDict):
    """One species at one beach in species_auto.json."""

    taxon_id: int
    observation_count: int


class AutoSpecies(TypedDict):
    """The whole of species_auto.json, minus its schema version."""

    taxa: dict[str, AutoTaxon]
    beaches: dict[str, list[BeachTaxon]]


class RankableTaxon(TypedDict):
    """The fields of a taxon that decide its place in the ranking."""

    taxon_id: int
    scientific_name: str
    iconic_group: str | None
    observation_count: int
    marine: bool
    photo: Photo | None


class SpeciesNote(Sourced):
    """A hand-written note about a species at one beach."""

    blurb: Localized
    sargassum_note: Localized | None


class Species(Sourced):
    """A species ready to display, with API data and notes already merged."""

    taxon_id: int
    scientific_name: str
    common_name: Localized
    iconic_group: str | None
    observation_count: int
    marine: bool
    photo: Photo | None
    blurb: Localized | None
    sargassum_note: Localized | None


class Vida(TypedDict):
    """The author's choices for a beach's species."""

    pinned_taxa: list[int]
    hidden_taxa: list[int]
    notes: dict[int, SpeciesNote]


class Teaser(Sourced):
    """One line shown while the panel is closed."""

    text: Localized


class Roca(Sourced):
    """What the rock and sand of a coastal unit are."""

    title: Localized
    story: Localized
    rock_type: str
    sand: Localized


class CoastalUnit(TypedDict):
    """A stretch of coast. Beaches point to one by its key."""

    name: Localized
    roca: Roca | None
    sargazo_por_que: Localized | None


class HistoriaItem(Sourced):
    """One episode in the history of the place."""

    title: Localized
    body: Localized
    year: int | None


class BeachProfile(TypedDict):
    """One entry of PROFILES. radius_km widens the species search for a beach."""

    coastal_unit: str
    teaser: Teaser | None
    vida: Vida
    historia: list[HistoriaItem]
    radius_km: NotRequired[int]


class BeachKnowledge(TypedDict):
    """Validated content for a beach, with its species already merged and ranked."""

    profile: BeachProfile | None
    photo: BeachPhoto | None
    unit: CoastalUnit | None
    teaser: Teaser | None
    species: list[Species]
    roca: Roca | None
    sargazo_por_que: Localized | None
    historia: list[HistoriaItem]


Checker = Callable[[object], list[str]]


def is_safe_url(url: object) -> bool:
    """True for an https URL with a host and no credentials or odd characters.

    Whitespace and control characters are refused before parsing because
    urlsplit silently strips some of them; backslashes because browsers treat
    them as slashes, which changes which host a URL really points to.
    """
    if not isinstance(url, str) or not url or len(url) > MAX_URL_CHARS:
        return False
    if _URL_FORBIDDEN_CHARS.intersection(url) or not url.isprintable():
        return False
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    return (
        parts.scheme == HTTPS_SCHEME
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
    )


def is_allowed_image_url(url: object) -> bool:
    """True only for a square thumbnail on an allowlisted image host."""
    if not is_safe_url(url):
        return False
    parts = urlsplit(url)
    return (
        parts.hostname in IMAGE_HOSTS
        and parts.port in (None, HTTPS_PORT)
        and IMAGE_PATH_RE.fullmatch(parts.path) is not None
    )


def observation_search_url(lat: float, lon: float, radius_km: int, taxon_id: int) -> str:
    """iNaturalist's search for this species near a beach: the fact's source."""
    query = urlencode({"lat": lat, "lng": lon, "radius": radius_km, "taxon_id": taxon_id})
    return f"{INATURALIST_OBSERVATIONS_URL}?{query}"


def _text_problems(path: str, value: object, limit: int) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{path}: must be non-empty text"]
    if _FORBIDDEN_TEXT_RE.search(value):
        return [f"{path}: has markup or control characters"]
    if len(value) > limit:
        return [f"{path}: {len(value)} chars exceeds the {limit} budget"]
    return []


def _shape_problems(path: str, record: object, shape: type) -> list[str]:
    """Check a record against a TypedDict: a dict with exactly the known keys."""
    if not isinstance(record, Mapping):
        return [f"{path}: must be an object"]
    keys = set(record)
    missing = shape.__required_keys__ - keys
    unknown = keys - shape.__required_keys__ - shape.__optional_keys__
    problems = [f"{path}: missing {key}" for key in sorted(missing)]
    problems += [f"{path}: unexpected key {key!r}" for key in sorted(map(str, unknown))]
    return problems


def _localized_problems(path: str, value: object, limit: int) -> list[str]:
    shape = _shape_problems(path, value, Localized)
    if shape:
        return shape
    return [p for lang in LANGUAGES for p in _text_problems(f"{path}.{lang}", value[lang], limit)]


def _optional_localized_problems(path: str, value: object, limit: int) -> list[str]:
    return [] if value is None else _localized_problems(path, value, limit)


def _source_problems(path: str, source: object) -> list[str]:
    shape = _shape_problems(path, source, Source)
    if shape:
        return shape
    problems = _text_problems(f"{path}.label", source["label"], MAX_SOURCE_LABEL_CHARS)
    if not is_safe_url(source["url"]):
        problems.append(f"{path}.url: not a safe https URL")
    return problems


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _sourced_problems(path: str, record: Mapping, methods: frozenset[str]) -> list[str]:
    """Enforce the rule that every fact has a method and at least one source."""
    problems: list[str] = []
    method = record["method"]
    if not isinstance(method, str) or method not in methods:
        problems.append(f"{path}.method: not an allowed method here")
    if method == METHOD_LLM_REVIEWED and "verified_at" not in record:
        problems.append(f"{path}: {METHOD_LLM_REVIEWED} needs verified_at")
    if "verified_at" in record and not _is_iso_date(record["verified_at"]):
        problems.append(f"{path}.verified_at: must be an ISO date")
    sources = record["sources"]
    if not isinstance(sources, list) or not sources:
        return [*problems, f"{path}.sources: at least one source is required"]
    if len(sources) > MAX_SOURCES_PER_FACT:
        problems.append(f"{path}.sources: more than {MAX_SOURCES_PER_FACT}")
    for index, source in enumerate(sources):
        problems += _source_problems(f"{path}.sources[{index}]", source)
    return problems


def _thumbnail_copy_problems(path: str, photo: Mapping) -> list[str]:
    fields = ("file", "width", "height", "sha256")
    if not any(field in photo for field in fields):
        return []
    if not all(field in photo for field in fields):
        return [f"{path}: file, width, height and sha256 go together"]
    problems: list[str] = []
    match = SPECIES_IMAGE_FILE_RE.fullmatch(photo["file"]) if isinstance(photo["file"], str) else None
    if match is None or match.group(1) != photo["sha256"]:
        problems.append(f"{path}.file: must be species/<sha256>.webp")
    if photo["width"] != THUMBNAIL_FILE_PX or photo["height"] != THUMBNAIL_FILE_PX:
        problems.append(f"{path}: thumbnails must be {THUMBNAIL_FILE_PX}x{THUMBNAIL_FILE_PX}")
    return problems


def photo_problems(photo: object) -> list[str]:
    """Return why a photo cannot be shown; the species stays either way."""
    path = "photo"
    shape = _shape_problems(path, photo, Photo)
    if shape:
        return shape
    problems = _text_problems(f"{path}.attribution", photo["attribution"], MAX_ATTRIBUTION_CHARS)
    license_code = photo["license_code"]
    if not isinstance(license_code, str) or license_code not in PHOTO_LICENSES:
        problems.append(f"{path}.license_code: not an allowed open license")
    if not is_allowed_image_url(photo["url"]):
        problems.append(f"{path}.url: not an allowed thumbnail URL")
    if not is_safe_url(photo["source_url"]):
        problems.append(f"{path}.source_url: not a safe https URL")
    return [*problems, *_thumbnail_copy_problems(path, photo)]


def _beach_photo_file_problems(path: str, record: Mapping) -> list[str]:
    problems: list[str] = []
    match = BEACH_IMAGE_FILE_RE.fullmatch(record["file"]) if isinstance(record["file"], str) else None
    if match is None or match.group(1) != record["sha256"]:
        problems.append(f"{path}.file: must be beaches/<sha256>.webp")
    width, height, size = record["width"], record["height"], record["bytes"]
    if not _is_int(width) or not _is_int(height) or not _is_int(size):
        return [*problems, f"{path}: width, height and bytes must be integers"]
    if not 0 < width <= MAX_BEACH_PHOTO_WIDTH or height <= 0:
        problems.append(f"{path}.width: must be at most {MAX_BEACH_PHOTO_WIDTH}")
    if not 0 < size <= MAX_BEACH_PHOTO_BYTES:
        problems.append(f"{path}.bytes: must be at most {MAX_BEACH_PHOTO_BYTES}")
    return problems


def beach_photo_problems(record: object) -> list[str]:
    """Return why a beach photo entry cannot ship; an empty list means it can."""
    path = "beach_photo"
    shape = _shape_problems(path, record, BeachPhoto)
    if shape:
        return shape
    problems = _beach_photo_file_problems(path, record)
    problems += _text_problems(f"{path}.author", record["author"], MAX_ATTRIBUTION_CHARS)
    problems += _localized_problems(f"{path}.alt", record["alt"], MAX_ALT_CHARS)
    if not isinstance(record["license"], str) or record["license"] not in PHOTO_LICENSES:
        problems.append(f"{path}.license: not an allowed open license")
    for field in ("license_url", "source_url"):
        if not is_safe_url(record[field]):
            problems.append(f"{path}.{field}: not a safe https URL")
    if not isinstance(record["source"], str) or record["source"] not in BEACH_PHOTO_SOURCES:
        problems.append(f"{path}.source: must be own or wikimedia_commons")
    year = record["taken_year"]
    if year is not None and not (_is_int(year) and MIN_PHOTO_YEAR <= year <= dt.date.today().year):
        problems.append(f"{path}.taken_year: must be a plausible year or null")
    if record["method"] != METHOD_CURATED:
        problems.append(f"{path}.method: must be curated")
    if not _is_iso_date(record["reviewed_at"]):
        problems.append(f"{path}.reviewed_at: must be an ISO date")
    return problems


def _optional_name_problems(path: str, names: object) -> list[str]:
    shape = _shape_problems(path, names, OptionalNames)
    if shape:
        return shape
    return [
        problem
        for lang in LANGUAGES
        if names[lang] is not None
        for problem in _text_problems(f"{path}.{lang}", names[lang], MAX_NAME_CHARS)
    ]


def _taxon_scalar_problems(record: Mapping) -> list[str]:
    path = "taxon"
    problems: list[str] = []
    if not _is_int(record["taxon_id"]):
        problems.append(f"{path}.taxon_id: must be an integer")
    if not isinstance(record["marine"], bool):
        problems.append(f"{path}.marine: must be true or false")
    if not isinstance(record["method"], str) or record["method"] not in AUTO_SPECIES_METHODS:
        problems.append(f"{path}.method: not an allowed method here")
    if not _is_iso_date(record["fetched_at"]):
        problems.append(f"{path}.fetched_at: must be an ISO date")
    if record["marine_tagged_at"] is not None and not _is_iso_date(record["marine_tagged_at"]):
        problems.append(f"{path}.marine_tagged_at: must be an ISO date")
    if record["iconic_group"] is not None:
        problems += _text_problems(f"{path}.iconic_group", record["iconic_group"], MAX_ICONIC_GROUP_CHARS)
    if record["marine_source"] is not None:
        problems += _text_problems(f"{path}.marine_source", record["marine_source"], MAX_ICONIC_GROUP_CHARS)
    return problems


def taxon_problems(record: object) -> list[str]:
    """Return why a taxon cannot ship. A bad photo is not a reason: see photo_problems."""
    shape = _shape_problems("taxon", record, AutoTaxon)
    if shape:
        return shape
    return [
        *_taxon_scalar_problems(record),
        *_text_problems("taxon.scientific_name", record["scientific_name"], MAX_NAME_CHARS),
        *_optional_name_problems("taxon.common_name", record["common_name"]),
    ]


def beach_taxon_problems(record: object) -> list[str]:
    """Return why a beach's reference to a species cannot ship."""
    shape = _shape_problems("beach_taxon", record, BeachTaxon)
    if shape:
        return shape
    if not _is_int(record["taxon_id"]):
        return ["beach_taxon.taxon_id: must be an integer"]
    count = record["observation_count"]
    return [] if _is_int(count) and count > 0 else ["beach_taxon.observation_count: must be positive"]


def auto_reference_problems(auto: Mapping) -> list[str]:
    """Return dangling references and species that no beach points to."""
    taxa, beaches = auto.get("taxa"), auto.get("beaches")
    if not isinstance(taxa, Mapping) or not isinstance(beaches, Mapping):
        return ["species file: taxa and beaches must be objects"]
    referenced = {
        str(reference.get("taxon_id"))
        for references in beaches.values() for reference in _as_list(references)
        if isinstance(reference, Mapping)
    }
    problems = [f"dangling reference to taxon {key}" for key in sorted(referenced - set(taxa))]
    problems += [f"taxon {key} is not referenced by any beach" for key in sorted(set(taxa) - referenced)]
    return problems


def teaser_problems(record: object) -> list[str]:
    """Return why a teaser cannot ship; an empty list means it can."""
    shape = _shape_problems("teaser", record, Teaser)
    if shape:
        return shape
    return _sourced_problems("teaser", record, HANDWRITTEN_METHODS) + _localized_problems(
        "teaser.text", record["text"], MAX_TEASER_CHARS
    )


def roca_problems(record: object) -> list[str]:
    """Return why a Roca block cannot ship; an empty list means it can."""
    path = "roca"
    shape = _shape_problems(path, record, Roca)
    if shape:
        return shape
    return [
        *_sourced_problems(path, record, ROCA_METHODS),
        *_localized_problems(f"{path}.title", record["title"], MAX_ROCA_TITLE_CHARS),
        *_localized_problems(f"{path}.story", record["story"], MAX_GEOLOGY_CHARS),
        *_localized_problems(f"{path}.sand", record["sand"], MAX_SAND_CHARS),
        *_text_problems(f"{path}.rock_type", record["rock_type"], MAX_ROCK_TYPE_CHARS),
    ]


def unit_problems(record: object) -> list[str]:
    """Return why a coastal unit cannot ship.

    The sargassum line has no sources of its own, so it needs a Roca block whose
    sources back it.
    """
    shape = _shape_problems("unit", record, CoastalUnit)
    if shape:
        return shape
    problems = _localized_problems("unit.name", record["name"], MAX_NAME_CHARS)
    if record["roca"] is not None:
        problems += roca_problems(record["roca"])
    problems += _optional_localized_problems("unit.sargazo_por_que", record["sargazo_por_que"], MAX_SARGAZO_WHY_CHARS)
    if record["sargazo_por_que"] is not None and record["roca"] is None:
        problems.append("unit.sargazo_por_que: needs a roca block with sources")
    return problems


def history_problems(record: object) -> list[str]:
    """Return why a history item cannot ship; an empty list means it can."""
    path = "historia"
    shape = _shape_problems(path, record, HistoriaItem)
    if shape:
        return shape
    problems = _sourced_problems(path, record, HANDWRITTEN_METHODS)
    problems += _localized_problems(f"{path}.title", record["title"], MAX_HISTORY_TITLE_CHARS)
    problems += _localized_problems(f"{path}.body", record["body"], MAX_HISTORY_BODY_CHARS)
    if record["year"] is not None and not _is_int(record["year"]):
        problems.append(f"{path}.year: must be an integer or null")
    return problems


def note_problems(record: object) -> list[str]:
    """Return why a species note cannot ship; an empty list means it can."""
    path = "note"
    shape = _shape_problems(path, record, SpeciesNote)
    if shape:
        return shape
    return [
        *_sourced_problems(path, record, HANDWRITTEN_METHODS),
        *_localized_problems(f"{path}.blurb", record["blurb"], MAX_BLURB_CHARS),
        *_optional_localized_problems(f"{path}.sargassum_note", record["sargassum_note"], MAX_SARGASSUM_NOTE_CHARS),
    ]


def vida_problems(record: object) -> list[str]:
    """Return why a beach's species choices cannot ship."""
    path = "vida"
    shape = _shape_problems(path, record, Vida)
    if shape:
        return shape
    problems: list[str] = []
    for field in ("pinned_taxa", "hidden_taxa"):
        ids = record[field]
        if not isinstance(ids, list) or not all(_is_int(taxon_id) for taxon_id in ids):
            problems.append(f"{path}.{field}: must be a list of integers")
    notes = record["notes"]
    if not isinstance(notes, Mapping):
        return [*problems, f"{path}.notes: must be an object"]
    for taxon_id, note in notes.items():
        if not _is_int(taxon_id):
            problems.append(f"{path}.notes: keys must be taxon ids")
        problems += note_problems(note)
    if not problems and set(record["pinned_taxa"]) & set(record["hidden_taxa"]):
        problems.append(f"{path}: a taxon is both pinned and hidden")
    return problems


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def profile_problems(raw: object, units: Mapping[str, object] = COASTAL_UNITS) -> list[str]:
    """Return every problem in a PROFILES entry. Used by the data tests."""
    shape = _shape_problems("profile", raw, BeachProfile)
    if shape:
        return shape
    problems: list[str] = []
    unit_key = raw["coastal_unit"]
    if not isinstance(unit_key, str) or unit_key not in units:
        problems.append("profile.coastal_unit: not a key in COASTAL_UNITS")
    if raw["teaser"] is not None:
        problems += teaser_problems(raw["teaser"])
    problems += vida_problems(raw["vida"])
    if not isinstance(raw["historia"], list):
        problems.append("historia: must be a list")
    for item in _as_list(raw["historia"]):
        problems += history_problems(item)
    if len(_as_list(raw["historia"])) > MAX_HISTORY_ITEMS:
        problems.append(f"historia: more than {MAX_HISTORY_ITEMS} items")
    if "radius_km" in raw and not (_is_int(raw["radius_km"]) and raw["radius_km"] > 0):
        problems.append("profile.radius_km: must be a positive integer")
    return problems


def _valid_records(beach: str, lens: str, records: Iterable[object], checker: Checker) -> list:
    """Keep the records that pass, logging each one dropped with its reasons."""
    kept = []
    for record in records:
        problems = checker(record)
        if problems:
            logger.warning("conoce: dropped %s for %r: %s", lens, beach, "; ".join(problems))
        else:
            kept.append(record)
    return kept


def _rank_key(taxon: RankableTaxon) -> tuple[int, int, str]:
    """Marine with a photo, then any photo, then the rest; most observed first."""
    if not taxon["photo"]:
        tier = TIER_NO_PHOTO
    elif taxon["marine"]:
        tier = TIER_MARINE_WITH_PHOTO
    else:
        tier = TIER_ANY_PHOTO
    return tier, -taxon["observation_count"], taxon["scientific_name"]


def _group_limit(group: str | None) -> int:
    return MAX_PLANTS_IN_TOP if group == PLANT_GROUP else MAX_SAME_GROUP_IN_TOP


def _diversified(ranked: list[RankableTaxon], pinned: list[RankableTaxon]) -> list[RankableTaxon]:
    """Fill the free featured slots within each group's limit.

    At most one plant and two of any other group, so a lens doesn't open on two
    plants. Pinned taxa count toward a group but are never moved. If the limits
    leave slots empty, the next ranked taxa fill them rather than leaving them short.
    """
    slots = max(0, MAX_FEATURED_SPECIES - len(pinned))
    groups = Counter(taxon["iconic_group"] for taxon in pinned[:MAX_FEATURED_SPECIES])
    top: list[RankableTaxon] = []
    rest: list[RankableTaxon] = []
    for taxon in ranked:
        group = taxon["iconic_group"]
        if len(top) < slots and groups[group] < _group_limit(group):
            top.append(taxon)
            groups[group] += 1
        else:
            rest.append(taxon)
    return [*top, *rest]


def rank_taxa(
    taxa: Iterable[RankableTaxon],
    pinned: Iterable[int] = (),
    hidden: Iterable[int] = (),
) -> list[RankableTaxon]:
    """Order taxa for display: pinned, then _rank_key, with the diversity rule.

    Pinned order is the author's and overrides everything, so a common species
    with no photo can still be pulled to the top. Hidden always wins over pinned.
    """
    hidden_ids = set(hidden)
    visible = {t["taxon_id"]: t for t in taxa if t["taxon_id"] not in hidden_ids}
    pinned_ids = [taxon_id for taxon_id in dict.fromkeys(pinned) if taxon_id in visible]
    pinned_taxa = [visible[taxon_id] for taxon_id in pinned_ids]
    others = [t for t in visible.values() if t["taxon_id"] not in set(pinned_ids)]
    return [*pinned_taxa, *_diversified(sorted(others, key=_rank_key), pinned_taxa)]


def effective_marine(taxon_id: int, tag: bool, overrides: Mapping[int, bool] = MARINE_OVERRIDES) -> bool:
    """The OBIS tag, unless a curated override corrects it."""
    return overrides.get(taxon_id, tag)


def _usable_photo(beach: str, photo: object) -> Photo | None:
    """The photo if it can be shown; otherwise the species stays, without one."""
    if photo is None:
        return None
    problems = photo_problems(photo)
    if problems:
        logger.warning("conoce: dropped photo for %r: %s", beach, "; ".join(problems))
        return None
    return photo


def _display_names(taxon: AutoTaxon) -> Localized:
    """Names per language, falling back to the scientific name."""
    return {lang: taxon["common_name"][lang] or taxon["scientific_name"] for lang in LANGUAGES}


def _merge_taxon(
    beach: str,
    reference: BeachTaxon,
    taxon: AutoTaxon,
    source: Source,
    overrides: Mapping[int, bool],
) -> Species:
    return {
        "taxon_id": taxon["taxon_id"],
        "scientific_name": taxon["scientific_name"],
        "common_name": _display_names(taxon),
        "iconic_group": taxon["iconic_group"],
        "observation_count": reference["observation_count"],
        "marine": effective_marine(taxon["taxon_id"], taxon["marine"], overrides),
        "photo": _usable_photo(beach, taxon["photo"]),
        "blurb": None,
        "sargassum_note": None,
        "method": taxon["method"],
        "sources": [source],
    }


def _with_note(species: Species, note: SpeciesNote | None) -> Species:
    if note is None:
        return species
    sources = [*species["sources"], *note["sources"]][:MAX_SOURCES_PER_FACT]
    return {**species, "blurb": note["blurb"], "sargassum_note": note["sargassum_note"], "sources": sources}


@lru_cache(maxsize=1)
def _beach_coordinates() -> Mapping[str, tuple[float, float]]:
    return MappingProxyType({b["name"]: (b["latitude"], b["longitude"]) for b in BEACHES})


def _valid_taxa(taxa: object) -> dict[int, AutoTaxon]:
    if not isinstance(taxa, Mapping):
        return {}
    kept = _valid_records("species_auto", "taxon", taxa.values(), taxon_problems)
    return {taxon["taxon_id"]: taxon for taxon in kept}


def _beach_species(
    beach: str,
    profile: BeachProfile | None,
    references: object,
    taxa: Mapping[int, AutoTaxon],
    overrides: Mapping[int, bool],
) -> list[Species]:
    """Merge a beach's API species with its notes, pins and hidden taxa."""
    coordinates = _beach_coordinates().get(beach)
    if coordinates is None:
        return []
    radius = profile.get("radius_km", DEFAULT_RADIUS_KM) if profile else DEFAULT_RADIUS_KM
    merged: list[Species] = []
    for reference in _valid_records(beach, "beach taxon", _as_list(references), beach_taxon_problems):
        taxon = taxa.get(reference["taxon_id"])
        if taxon is not None:
            url = observation_search_url(*coordinates, radius, taxon["taxon_id"])
            merged.append(_merge_taxon(beach, reference, taxon, {"label": INATURALIST_LABEL, "url": url}, overrides))
    vida = profile["vida"] if profile else {"pinned_taxa": [], "hidden_taxa": [], "notes": {}}
    ranked = rank_taxa(merged, vida["pinned_taxa"], vida["hidden_taxa"])[:MAX_SPECIES_TOTAL]
    return [_with_note(species, vida["notes"].get(species["taxon_id"])) for species in ranked]


def _valid_profile(beach: str, raw: object, units: Mapping[str, object]) -> BeachProfile | None:
    """The profile with each bad piece dropped, or None if it cannot be used."""
    if raw is None:
        return None
    if _shape_problems("profile", raw, BeachProfile) or vida_problems(raw["vida"]):
        logger.warning("conoce: dropped profile for %r", beach)
        return None
    historia = _valid_records(beach, "historia", _as_list(raw["historia"]), history_problems)
    teaser = raw["teaser"]
    if teaser is not None and teaser_problems(teaser):
        logger.warning("conoce: dropped teaser for %r", beach)
        teaser = None
    unit_key = raw["coastal_unit"] if isinstance(raw["coastal_unit"], str) else ""
    return {**raw, "coastal_unit": unit_key if unit_key in units else "", "teaser": teaser, "historia": historia[:MAX_HISTORY_ITEMS]}


def _valid_unit(key: str, units: Mapping[str, object]) -> CoastalUnit | None:
    unit = units.get(key)
    if unit is None:
        return None
    problems = unit_problems(unit)
    if problems:
        logger.warning("conoce: dropped coastal unit %r: %s", key, "; ".join(problems))
        return None
    return unit


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _usable_beach_photo(beach: str, record: object, static_dir: Path) -> BeachPhoto | None:
    """The entry if it is valid and its file is intact. Skipped entries are logged
    here, at load, which happens once per process.
    """
    if record is None:
        return None
    problems = beach_photo_problems(record)
    if problems:
        logger.warning("conoce: dropped beach photo for %r: %s", beach, "; ".join(problems))
        return None
    path = static_dir / record["file"]
    try:
        digest = file_sha256(path)
    except OSError as exc:
        logger.warning("conoce: beach photo for %r is unreadable: %s", beach, exc)
        return None
    if digest != record["sha256"]:
        logger.warning("conoce: beach photo for %r does not match its sha256", beach)
        return None
    return record


def _build_knowledge(
    beach: str,
    profile: BeachProfile | None,
    units: Mapping[str, object],
    species: list[Species],
    photo: BeachPhoto | None,
) -> BeachKnowledge:
    unit = _valid_unit(profile["coastal_unit"], units) if profile else None
    return {
        "profile": profile,
        "photo": photo,
        "unit": unit,
        "teaser": profile["teaser"] if profile else None,
        "species": species,
        "roca": unit["roca"] if unit else None,
        "sargazo_por_que": unit["sargazo_por_que"] if unit else None,
        "historia": profile["historia"] if profile else [],
    }


def build_index(
    profiles: Mapping[str, object],
    units: Mapping[str, object],
    auto: Mapping[str, object],
    overrides: Mapping[int, bool] = MARINE_OVERRIDES,
    photos: Mapping[str, object] | None = None,
    static_dir: Path = STATIC_DIR,
) -> dict[str, BeachKnowledge]:
    """Build the per-beach index, with species merged and ranked once, here."""
    photos = photos or {}
    taxa = _valid_taxa(auto.get("taxa"))
    references = auto.get("beaches")
    references = references if isinstance(references, Mapping) else {}
    index: dict[str, BeachKnowledge] = {}
    for beach in {*profiles, *references, *photos}:
        profile = _valid_profile(beach, profiles.get(beach), units)
        species = _beach_species(beach, profile, references.get(beach), taxa, overrides)
        photo = _usable_beach_photo(beach, photos.get(beach), static_dir)
        index[beach] = _build_knowledge(beach, profile, units, species, photo)
    return index


def read_auto_species(path: Path = AUTO_SPECIES_PATH) -> AutoSpecies:
    """Read the generated species file. Failing to read it hides only that data."""
    empty: AutoSpecies = {"taxa": {}, "beaches": {}}
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("conoce: could not read %s: %s", path.name, exc)
        return empty
    if not isinstance(payload, dict) or payload.get("schema_version") != AUTO_SCHEMA_VERSION:
        logger.warning("conoce: %s has an unexpected shape or version", path.name)
        return empty
    taxa, beaches = payload.get("taxa"), payload.get("beaches")
    if not isinstance(taxa, dict) or not isinstance(beaches, dict):
        logger.warning("conoce: %s has an unexpected shape", path.name)
        return empty
    return {"taxa": taxa, "beaches": beaches}


def read_beach_photos(path: Path = BEACH_PHOTOS_PATH) -> dict[str, object]:
    """Read the beach photo file. Failing to read it hides only the photos."""
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("conoce: could not read %s: %s", path.name, exc)
        return {}
    if not isinstance(payload, dict):
        logger.warning("conoce: %s has an unexpected shape", path.name)
        return {}
    return payload


@lru_cache(maxsize=1)
def load_index() -> Mapping[str, BeachKnowledge]:
    """Load and index the data files once per process. Treat it as read-only.

    This is lru_cache rather than st.cache_data on purpose. st.cache_data pickles
    its value and unpickles a fresh copy on every hit, so caching the index that
    way would copy every beach on every Streamlit rerun, and a dict lookup on the
    copy would not make that cost O(1). lru_cache shares one object, and it also
    works in the API, which has no Streamlit.
    """
    return MappingProxyType(build_index(
        PROFILES, COASTAL_UNITS, read_auto_species(), photos=read_beach_photos(),
    ))


def knowledge_for(beach_name: str) -> BeachKnowledge | None:
    """O(1) lookup of a beach's validated content, for rendering."""
    return load_index().get(beach_name)


def beach_photo(beach_name: str) -> BeachPhoto | None:
    """The beach's reference photo, if it is valid and its file is intact."""
    knowledge = knowledge_for(beach_name)
    return knowledge["photo"] if knowledge else None


def get_profile(beach_name: str) -> BeachProfile | None:
    """The beach's validated profile."""
    knowledge = knowledge_for(beach_name)
    return knowledge["profile"] if knowledge else None


def featured_species(beach_name: str, limit: int = MAX_FEATURED_SPECIES) -> list[Species]:
    """The beach's species in display order. Disallowed photos are already gone."""
    knowledge = knowledge_for(beach_name)
    return knowledge["species"][:limit] if knowledge else []


def coastal_unit_for(beach_name: str) -> CoastalUnit | None:
    """The coastal unit the beach points to."""
    knowledge = knowledge_for(beach_name)
    return knowledge["unit"] if knowledge else None


def has_content(beach_name: str) -> dict[str, bool]:
    """Which lenses have authored or API content, ignoring the catalogue lines."""
    knowledge = knowledge_for(beach_name)
    if not knowledge:
        return {"vida": False, "roca": False, "historia": False}
    return {
        "vida": bool(knowledge["species"]),
        "roca": knowledge["roca"] is not None,
        "historia": bool(knowledge["historia"]),
    }
