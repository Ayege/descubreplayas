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

"""Fill dashboard/data/species_auto.json from iNaturalist and OBIS.

Run by hand, never from the 6-hour pipeline:

    python scripts/fetch_species.py --dry-run --beach "Playa Bávaro"
    python scripts/fetch_species.py

Endpoints, fields and limits are documented in docs/data-cards/species_auto.md.
Every response is treated as untrusted. Photos are reference photos of the
species, not photos taken at the beach.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import random
import re
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.beach_profiles import PROFILES  # noqa: E402  (sys.path set above)
from dashboard.beaches_data import BEACHES  # noqa: E402
from dashboard.conoce_data import (  # noqa: E402
    AUTO_SCHEMA_VERSION,
    AUTO_SPECIES_PATH,
    DEFAULT_RADIUS_KM,
    MAX_ATTRIBUTION_CHARS,
    MAX_AUTO_SPECIES_BYTES,
    MAX_ICONIC_GROUP_CHARS,
    MAX_NAME_CHARS,
    METHOD_INATURALIST,
    PHOTO_LICENSES,
    AutoSpecies,
    AutoTaxon,
    BeachTaxon,
    OptionalNames,
    Photo,
    effective_marine,
    is_allowed_image_url,
    rank_taxa,
    read_auto_species,
)

logger = logging.getLogger(__name__)

USER_AGENT = "DescubrePlayasRD/1.0 (+https://github.com/Ayege/descubreplayas)"
INAT_SPECIES_COUNTS_URL = "https://api.inaturalist.org/v1/observations/species_counts"
INAT_TAXA_URL = "https://api.inaturalist.org/v1/taxa"
INAT_PHOTO_PAGE_URL = "https://www.inaturalist.org/photos"
OBIS_CHECKLIST_URL = "https://api.obis.org/v3/checklist"

# Beaches left out of the run. Each is reported as skipped, never silently missing.
SKIP_BEACHES = MappingProxyType({
    # Its province and description place it near Amber Cove, but its coordinates
    # in beaches_data.py point near Río San Juan, about 70 km east, so its species
    # would come from the wrong coast. Remove once the coordinates are confirmed.
    "Playa Maimón": "coordinates point to the wrong coast",
})

SPECIES_PER_PAGE = 200  # The API's maximum is 500.
TAXA_BATCH_SIZE = 30  # The API returns at most 30 taxa per request.
OBIS_BATCH_SIZE = 10  # At or below the 10 results OBIS was seen returning per page.
MAX_CANDIDATES_PER_BEACH = 12
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_AGE_DAYS = 25

CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 60.0
REQUEST_DELAY_S = 1.1  # iNaturalist asks for about one request per second.
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 3.0
MAX_RETRY_WAIT_S = 60.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
HTTP_OK = 200

SENSITIVE_GEOPRIVACY = frozenset({"obscured", "private"})
DOMINICAN_REPUBLIC_PLACE_ID = 6931  # Read from the API's conservation_statuses.
EXCLUDED_BY_STATUS = "status"
EXCLUDED_LOOKUP_FAILED = "lookup_failed"
ORIGIN_DEFAULT_PHOTO = "default_photo"
ORIGIN_TAXON_PHOTOS = "taxon_photos"
MARINE_SOURCE_OBIS = "obis"
OBIS_NO_MATCH_MARKER = b"NAME_NOT_FOUND"

_TAG_RE = re.compile(r"<[^>]*>")
_UNSAFE_TEXT_RE = re.compile(r"[<>\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]")
_WHITESPACE_RE = re.compile(r"\s+")


class FetchError(Exception):
    """A request failed for good, or its response could not be trusted."""


class MarineTag(NamedTuple):
    """OBIS's answer for one name. A failed lookup is stored as None instead."""

    marine: bool


@dataclass(frozen=True)
class Api:
    """The one HTTP client for a run, and how to wait between requests."""

    client: httpx.Client
    sleep: Callable[[float], None] = time.sleep


@dataclass
class Caches:
    """Lookups already made, so a species shared by beaches is fetched once."""

    details: dict[int, dict | None] = field(default_factory=dict)
    marine: dict[str, MarineTag | None] = field(default_factory=dict)


@dataclass
class BeachResult:
    """One beach's output and the numbers for the summary table."""

    references: list[BeachTaxon]
    taxa: dict[int, AutoTaxon]
    total: int
    returned: int
    excluded_by_status: int
    excluded_lookup_failed: int
    eligible: int
    photos_default: int
    photos_taxon: int
    marine: int
    top: list[str]


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """One pooled client with explicit timeouts. TLS checks stay on by default."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        follow_redirects=False,
        transport=transport,
    )


def _read_capped(response: httpx.Response) -> bytes:
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise FetchError(f"response is over the {MAX_RESPONSE_BYTES} byte cap")
    return bytes(body)


def _retry_wait(headers: Mapping[str, str], attempt: int) -> float:
    """Honour Retry-After; otherwise back off exponentially, with jitter."""
    try:
        wait = float(headers.get("Retry-After", ""))
    except ValueError:
        wait = BACKOFF_BASE_S * 2 ** attempt
    return min(wait + random.random(), MAX_RETRY_WAIT_S)


def _parse_object(body: bytes) -> dict:
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise FetchError("response is not JSON") from exc
    if not isinstance(payload, dict):
        raise FetchError("response is not a JSON object")
    return payload


def get_json(
    api: Api,
    url: str,
    params: Mapping[str, Any] | None = None,
    no_match: Callable[[int, bytes], bool] | None = None,
) -> dict:
    """GET one JSON object, retrying only 429 and 5xx, at most MAX_ATTEMPTS times.

    no_match recognises a failure response that really means "nothing found": it
    returns an empty object at once instead of being retried.
    """
    for attempt in range(MAX_ATTEMPTS):
        api.sleep(REQUEST_DELAY_S)
        with api.client.stream("GET", url, params=params) as response:
            body = _read_capped(response)
            status, headers = response.status_code, response.headers
        if status == HTTP_OK:
            return _parse_object(body)
        if no_match and no_match(status, body):
            return {}
        if status not in RETRY_STATUSES:
            raise FetchError(f"{url} returned HTTP {status}")
        logger.warning("fetch_species: %s returned HTTP %s, attempt %s", url, status, attempt + 1)
        api.sleep(_retry_wait(headers, attempt))
    raise FetchError(f"{url} kept failing after {MAX_ATTEMPTS} attempts")


def fetch_species_counts(api: Api, beach: Mapping, radius_km: int) -> dict:
    """The species seen near a beach. threatened is not sent on purpose: privacy
    is decided by the source's own geoprivacy flags, not by threat status.
    """
    return get_json(api, INAT_SPECIES_COUNTS_URL, {
        "lat": beach["latitude"],
        "lng": beach["longitude"],
        "radius": radius_km,
        "quality_grade": "research",
        "rank": "species",
        "photos": "true",
        "captive": "false",
        "geoprivacy": "open",
        "taxon_geoprivacy": "open",
        "per_page": SPECIES_PER_PAGE,
        "locale": "es",
    })


def _batches(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def fetch_taxa_details(api: Api, taxon_ids: Iterable[int], cache: dict[int, dict | None]) -> None:
    """Fill the cache with /taxa details. A failed batch caches None for its ids."""
    missing = sorted({taxon_id for taxon_id in taxon_ids if taxon_id not in cache})
    for batch in _batches(missing, TAXA_BATCH_SIZE):
        cache.update(dict.fromkeys(batch))
        try:
            payload = get_json(api, f"{INAT_TAXA_URL}/{','.join(map(str, batch))}")
        except (FetchError, httpx.HTTPError) as exc:
            logger.warning("fetch_species: taxa batch failed: %s", exc)
            continue
        for detail in _as_list(payload.get("results")):
            if isinstance(detail, dict) and detail.get("id") in cache:
                cache[detail["id"]] = detail


def _obis_no_match(status: int, body: bytes) -> bool:
    """OBIS answers HTTP 500 NAME_NOT_FOUND when no name in a batch matches."""
    return status == 500 and OBIS_NO_MATCH_MARKER in body


def _is_marine_record(record: Mapping) -> bool:
    return record.get("is_marine") is True or record.get("is_brackish") is True


def fetch_marine_tags(api: Api, names: Iterable[str], cache: dict[str, MarineTag | None]) -> None:
    """Tag names through OBIS. Absent from OBIS means not marine; a failed batch
    caches None, meaning unknown. No coordinates are sent.
    """
    missing = sorted({name for name in names if name not in cache})
    for batch in _batches(missing, OBIS_BATCH_SIZE):
        try:
            payload = get_json(api, OBIS_CHECKLIST_URL, {"scientificname": ",".join(batch)}, _obis_no_match)
        except (FetchError, httpx.HTTPError) as exc:
            logger.warning("fetch_species: OBIS batch failed: %s", exc)
            cache.update(dict.fromkeys(batch))
            continue
        results = _as_list(payload.get("results"))
        if isinstance(payload.get("total"), int) and payload["total"] > len(results):
            logger.warning("fetch_species: OBIS returned %s of %s results", len(results), payload["total"])
        found = {
            key: _is_marine_record(record)
            for record in results if isinstance(record, dict)
            for key in (record.get("scientificName"), record.get("acceptedNameUsage")) if key
        }
        cache.update({name: MarineTag(found.get(name, False)) for name in batch})


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def clean_text(value: object, limit: int) -> str | None:
    """Plain text with no tags or control characters, capped; None if nothing is left."""
    if not isinstance(value, str):
        return None
    text = _UNSAFE_TEXT_RE.sub(" ", _TAG_RE.sub(" ", value))
    text = _WHITESPACE_RE.sub(" ", text).strip()[:limit].strip()
    return text or None


def _clean_name(value: object) -> str | None:
    """A common name with its first letter capitalised, as the API's vary."""
    name = clean_text(value, MAX_NAME_CHARS)
    return name[0].upper() + name[1:] if name else None


def parse_photo(raw: object, origin: str) -> Photo | None:
    """A photo that passes every check, using only that photo object's own data."""
    if not isinstance(raw, Mapping):
        return None
    photo_id, url, license_code = raw.get("id"), raw.get("square_url"), raw.get("license_code")
    if not isinstance(photo_id, int) or isinstance(photo_id, bool):
        return None
    if not isinstance(license_code, str) or license_code not in PHOTO_LICENSES:
        return None
    if not is_allowed_image_url(url):
        return None
    attribution = clean_text(raw.get("attribution_name") or raw.get("attribution"), MAX_ATTRIBUTION_CHARS)
    if attribution is None:
        return None
    return {
        "url": url,
        "license_code": license_code,
        "attribution": attribution,
        "source_url": f"{INAT_PHOTO_PAGE_URL}/{photo_id}",
        "origin": origin,
    }


def _taxon_photo_objects(detail: object) -> Iterator[object]:
    """taxon_photos is undocumented, so anything unexpected simply yields nothing."""
    entries = detail.get("taxon_photos") if isinstance(detail, Mapping) else None
    for entry in _as_list(entries):
        if isinstance(entry, Mapping):
            yield entry.get("photo")


def choose_photo(taxon: Mapping, detail: object) -> Photo | None:
    """The default photo if it passes, else the first taxon photo that does."""
    photo = parse_photo(taxon.get("default_photo"), ORIGIN_DEFAULT_PHOTO)
    if photo:
        return photo
    for raw in _taxon_photo_objects(detail):
        photo = parse_photo(raw, ORIGIN_TAXON_PHOTOS)
        if photo:
            return photo
    return None


def _status_obscures_here(status: Mapping) -> bool:
    """True for an obscuring status that applies globally or to the DR."""
    if status.get("geoprivacy") not in SENSITIVE_GEOPRIVACY:
        return False
    place = status.get("place")
    if place is None:
        return status.get("place_id") in (None, DOMINICAN_REPUBLIC_PLACE_ID)
    return not isinstance(place, Mapping) or place.get("id") == DOMINICAN_REPUBLIC_PLACE_ID


def privacy_exclusion(taxon: Mapping, detail: object) -> str | None:
    """Why iNaturalist's own flags exclude this species, or None if they don't.

    If the flags cannot be read, fail closed: unlike a missing photo, a missing
    privacy answer could reveal a sensitive species. That is counted separately,
    so a flaky run cannot quietly empty a beach.
    """
    statuses = detail.get("conservation_statuses") if isinstance(detail, Mapping) else None
    if not isinstance(statuses, list) or not all(isinstance(status, Mapping) for status in statuses):
        return EXCLUDED_LOOKUP_FAILED
    summary = taxon.get("conservation_status")
    if isinstance(summary, Mapping):
        statuses = [*statuses, summary]
    return EXCLUDED_BY_STATUS if any(_status_obscures_here(status) for status in statuses) else None


def _parse_row(row: object) -> dict | None:
    """A species_counts row as a candidate, or None if it has the wrong shape."""
    if not isinstance(row, Mapping) or not isinstance(row.get("taxon"), Mapping):
        return None
    taxon, count = row["taxon"], row.get("count")
    taxon_id = taxon.get("id")
    name = clean_text(taxon.get("name"), MAX_NAME_CHARS)
    if not isinstance(taxon_id, int) or isinstance(taxon_id, bool) or name is None:
        return None
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return None
    names: OptionalNames = {
        "es": _clean_name(taxon.get("preferred_common_name")),
        "en": _clean_name(taxon.get("english_common_name")),
    }
    return {
        "taxon_id": taxon_id,
        "scientific_name": name,
        "common_name": names,
        "iconic_group": clean_text(taxon.get("iconic_taxon_name"), MAX_ICONIC_GROUP_CHARS),
        "observation_count": count,
        "marine": False,
        "photo": None,
        "raw_taxon": taxon,
    }


def _apply_tags(candidates: Iterable[dict], cache: Mapping[str, MarineTag | None]) -> None:
    for candidate in candidates:
        tag = cache.get(candidate["scientific_name"])
        candidate["marine_tag"] = tag
        candidate["marine"] = effective_marine(candidate["taxon_id"], bool(tag and tag.marine))


def _taxon_record(candidate: Mapping, today: str) -> AutoTaxon:
    tag: MarineTag | None = candidate["marine_tag"]
    return {
        "taxon_id": candidate["taxon_id"],
        "scientific_name": candidate["scientific_name"],
        "common_name": candidate["common_name"],
        "iconic_group": candidate["iconic_group"],
        "marine": bool(tag and tag.marine),
        "marine_source": MARINE_SOURCE_OBIS if tag else None,
        "marine_tagged_at": today if tag else None,
        "photo": candidate["photo"],
        "method": METHOD_INATURALIST,
        "fetched_at": today,
    }


def _eligible_candidates(rows: list[dict], caches: Caches) -> tuple[list[dict], Counter]:
    """Drop species excluded by privacy, then attach the best allowed photo."""
    eligible: list[dict] = []
    excluded: Counter = Counter()
    for candidate in rows:
        detail = caches.details.get(candidate["taxon_id"])
        reason = privacy_exclusion(candidate["raw_taxon"], detail)
        if reason:
            excluded[reason] += 1
            continue
        candidate["photo"] = choose_photo(candidate["raw_taxon"], detail)
        eligible.append(candidate)
    return eligible, excluded


def run_beach(api: Api, beach: Mapping, profile: Mapping, caches: Caches, today: str) -> BeachResult:
    """Fetch, validate and rank one beach. Raises FetchError if it cannot be done.

    Only species with a photo need a marine tag to rank, so the others are looked
    up after ranking, and only if they made the candidate list.
    """
    payload = fetch_species_counts(api, beach, profile.get("radius_km", DEFAULT_RADIUS_KM))
    results = payload.get("results")
    if not isinstance(results, list):
        raise FetchError("species_counts has no results list")
    rows = [row for row in map(_parse_row, results) if row]
    fetch_taxa_details(api, [row["taxon_id"] for row in rows], caches.details)
    eligible, excluded = _eligible_candidates(rows, caches)
    if excluded[EXCLUDED_LOOKUP_FAILED]:
        logger.warning("fetch_species: %s: %s species excluded because the lookup failed",
                       beach["name"], excluded[EXCLUDED_LOOKUP_FAILED])
    if rows and not eligible and excluded[EXCLUDED_LOOKUP_FAILED]:
        raise FetchError("every species lookup failed, so existing data is kept")
    fetch_marine_tags(api, [c["scientific_name"] for c in eligible if c["photo"]], caches.marine)
    _apply_tags(eligible, caches.marine)
    vida = profile["vida"]
    ranked = rank_taxa(eligible, vida["pinned_taxa"], vida["hidden_taxa"])[:MAX_CANDIDATES_PER_BEACH]
    fetch_marine_tags(api, [c["scientific_name"] for c in ranked], caches.marine)
    _apply_tags(ranked, caches.marine)
    return BeachResult(
        references=[{"taxon_id": c["taxon_id"], "observation_count": c["observation_count"]} for c in ranked],
        taxa={c["taxon_id"]: _taxon_record(c, today) for c in ranked},
        total=payload.get("total_results", len(results)),
        returned=len(results),
        excluded_by_status=excluded[EXCLUDED_BY_STATUS],
        excluded_lookup_failed=excluded[EXCLUDED_LOOKUP_FAILED],
        eligible=len(eligible),
        photos_default=sum(1 for c in eligible if c["photo"] and c["photo"]["origin"] == ORIGIN_DEFAULT_PHOTO),
        photos_taxon=sum(1 for c in eligible if c["photo"] and c["photo"]["origin"] == ORIGIN_TAXON_PHOTOS),
        marine=sum(1 for c in eligible if c["marine"]),
        top=[c["common_name"]["es"] or c["scientific_name"] for c in ranked[:3]],
    )


def is_fresh(existing: AutoSpecies, beach_name: str, today: dt.date, max_age_days: int) -> bool:
    """True when every species of the beach was fetched within max_age_days."""
    references = existing["beaches"].get(beach_name)
    if not references:
        return False
    for reference in references:
        taxon = existing["taxa"].get(str(reference.get("taxon_id")))
        if not taxon:
            return False
        try:
            age = today - dt.date.fromisoformat(taxon["fetched_at"])
        except (KeyError, TypeError, ValueError):
            return False
        if age.days > max_age_days:
            return False
    return True


def merge_results(existing: AutoSpecies, results: Mapping[str, BeachResult]) -> dict:
    """The new file contents. Untouched beaches stay; unused species are dropped."""
    beaches = {**existing["beaches"], **{name: r.references for name, r in results.items()}}
    taxa = dict(existing["taxa"])
    for result in results.values():
        taxa.update({str(taxon_id): record for taxon_id, record in result.taxa.items()})
    used = {str(ref["taxon_id"]) for refs in beaches.values() for ref in refs}
    return {
        "schema_version": AUTO_SCHEMA_VERSION,
        "taxa": {key: record for key, record in taxa.items() if key in used},
        "beaches": beaches,
    }


def serialise(payload: Mapping) -> str:
    """Sorted keys and stable order, so git diffs stay readable."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def write_atomic(path: Path, text: str) -> None:
    """Write through a temp file in the same directory, so a crash or failure
    never leaves a half-written file.
    """
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except OSError:
        Path(handle.name).unlink(missing_ok=True)
        raise


def collect(
    api: Api,
    beaches: Iterable[Mapping],
    profiles: Mapping[str, Mapping],
    existing: AutoSpecies,
    today: dt.date,
    max_age_days: int,
) -> dict[str, BeachResult]:
    """Run each beach. One failed beach logs a warning and does not stop the run."""
    caches = Caches()
    results: dict[str, BeachResult] = {}
    for beach in beaches:
        name = beach["name"]
        if is_fresh(existing, name, today, max_age_days):
            logger.info("fetch_species: skipping %s, fetched within %s days", name, max_age_days)
            continue
        try:
            results[name] = run_beach(api, beach, profiles[name], caches, today.isoformat())
        except (FetchError, httpx.HTTPError, KeyError) as exc:
            logger.warning("fetch_species: skipping %s: %s", name, exc)
    return results


def format_summary(results: Mapping[str, BeachResult], skipped: Iterable[str] = ()) -> str:
    """The run table. x_st and x_lk count species excluded by a privacy status and
    because the lookup failed. iNaturalist's own filter removes others before they
    ever reach this script, so those are not counted here.
    """
    header = (
        f"{'beach':30} {'total':>5} {'got':>4} {'x_st':>4} {'x_lk':>4} {'elig':>4} "
        f"{'p_def':>5} {'p_tax':>5} {'none':>4} {'marine':>6} {'kept':>4}  top 3"
    )
    lines = [header]
    for name, r in results.items():
        no_photo = r.eligible - r.photos_default - r.photos_taxon
        lines.append(
            f"{name[:30]:30} {r.total:>5} {r.returned:>4} {r.excluded_by_status:>4} "
            f"{r.excluded_lookup_failed:>4} {r.eligible:>4} "
            f"{r.photos_default:>5} {r.photos_taxon:>5} {no_photo:>4} {r.marine:>6} {len(r.references):>4}  "
            + ", ".join(r.top)
        )
    lines += [f"SKIPPED {name}: {SKIP_BEACHES[name]}" for name in skipped]
    return "\n".join(lines)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--beach", action="append", help="refresh only this beach (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="print the summary, write nothing")
    parser.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE_DAYS, help="skip beaches fetched within N days")
    parser.add_argument("--output", type=Path, default=AUTO_SPECIES_PATH, help="file to write")
    return parser.parse_args(argv)


def _select_beaches(names: list[str] | None) -> list[Mapping]:
    if not names:
        return list(BEACHES)
    by_name = {b["name"]: b for b in BEACHES}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise SystemExit(f"Unknown beach: {', '.join(unknown)}")
    return [by_name[name] for name in names]


def split_skipped(beaches: Iterable[Mapping]) -> tuple[list[Mapping], list[str]]:
    """Separate the beaches to run from those in SKIP_BEACHES."""
    selected = list(beaches)
    run = [beach for beach in selected if beach["name"] not in SKIP_BEACHES]
    return run, [beach["name"] for beach in selected if beach["name"] in SKIP_BEACHES]


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None,
         sleep: Callable[[float], None] = time.sleep) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    args = _parse_args(argv)
    beaches, skipped = split_skipped(_select_beaches(args.beach))
    existing = read_auto_species(args.output)
    with make_client(transport) as client:
        results = collect(Api(client, sleep), beaches, PROFILES, existing, dt.date.today(), args.max_age)
    print(format_summary(results, skipped))
    if args.dry_run:
        return 0
    text = serialise(merge_results(existing, results))
    size = len(text.encode("utf-8"))
    if size > MAX_AUTO_SPECIES_BYTES:
        logger.error("fetch_species: %s bytes is over the %s budget; nothing written", size, MAX_AUTO_SPECIES_BYTES)
        return 1
    write_atomic(args.output, text)
    logger.info("fetch_species: wrote %s (%s bytes, %s beaches)", args.output, size, len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
