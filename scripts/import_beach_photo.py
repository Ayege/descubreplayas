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

"""Add one reviewed photo to a beach. The only script that writes beach photos.

Commons photos have their licence and author fetched again here, never taken from the
candidates file. Every image is re-encoded with no metadata. Run it with the ingest
environment, from the repo root:

    .venv-ingest/bin/python scripts/import_beach_photo.py --beach "Playa Bávaro" \
        --commons-file "File:Name.jpg" --alt-es "..." --alt-en "..."
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.beaches_data import BEACHES  # noqa: E402  (sys.path set above)
from dashboard.conoce_data import (  # noqa: E402
    BEACH_IMAGE_FILE_RE,
    BEACH_PHOTOS_PATH,
    FORBIDDEN_TEXT_RE,
    MAX_ALT_CHARS,
    MAX_STATIC_BYTES,
    METHOD_CURATED,
    MIN_PHOTO_YEAR,
    PHOTO_LICENSES,
    STATIC_DIR,
    beach_photo_problems,
)
from scripts._commons import (  # noqa: E402
    DOWNLOAD_HOSTS,
    RETRY_STATUSES_NO_429,
    Candidate,
    ManualCheck,
    fetch_file,
    normalise_title,
)
from scripts._http import Api, FetchError, RateLimited, make_client  # noqa: E402
from scripts._images import (  # noqa: E402
    MODE_PHOTO,
    SUBDIR_BEACHES,
    ImageError,
    Processed,
    download,
    process,
    save,
)
from scripts.fetch_species import serialise, write_atomic  # noqa: E402

OWN_PHOTO_AUTHOR = "Ayesha Yege"
OWN_PHOTO_LICENSE = "cc-by"
OWN_PHOTO_SOURCE_URL = "https://github.com/Ayege/descubreplayas"
OWN_PHOTO_MAX_BYTES = 30 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_MANIFEST_PHOTOS = 100
MANIFEST_KEYS = frozenset({"beach", "commons_file", "own", "year", "alt_es", "alt_en"})
SOURCE_OWN = "own"
SOURCE_COMMONS = "wikimedia_commons"

CHECKLIST = """\
Please confirm:
  [ ] no identifiable person, no child
  [ ] the beach is recognisable
  [ ] the alt text describes the scene, not the file
  [ ] the year in the caption comes from the source, not a guess"""


class Refused(Exception):
    """The import can't go ahead. Nothing has been written."""

    def __init__(self, message: str, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


class PhotoSource(NamedTuple):
    """Where the pixels and the credit come from."""

    image: Processed
    author: str
    license_code: str
    license_url: str
    source_url: str
    source: str
    year: int | None


def _read_photos_strict(path: Path) -> dict:
    """Unlike the dashboard's reader, a damaged file stops the import. Treating it as
    empty would overwrite every other beach's entry.
    """
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"{path.name} can't be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise Refused(f"{path.name} is not a JSON object")
    return payload


def _from_commons(api: Api, file_arg: str) -> PhotoSource:
    title = normalise_title(file_arg)
    if title is None:
        raise Refused('--commons-file must look like "File:Name.jpg"')
    try:
        result = fetch_file(api, title)
        if isinstance(result, ManualCheck):
            raise Refused(f"licence name and URL disagree ({result.license_name} / {result.license_url}); check by hand")
        if not isinstance(result, Candidate):
            raise Refused(f"{title}: {result.reason}")
        raw = download(api, result.thumbnail_url, DOWNLOAD_HOSTS, RETRY_STATUSES_NO_429)
        image = process(raw, MODE_PHOTO)
    except RateLimited as exc:
        raise Refused(f"HTTP 429, wrong size or rate limited; not retrying: {exc}", rate_limited=True) from exc
    except (FetchError, ImageError, httpx.HTTPError) as exc:
        raise Refused(f"{title}: {exc}") from exc
    return PhotoSource(
        image, result.author, result.license.code, result.license.url, result.source_url, SOURCE_COMMONS, result.year
    )


def _from_own(path: Path, year: int, today: dt.date) -> PhotoSource:
    if not MIN_PHOTO_YEAR <= year <= today.year:
        raise Refused(f"--year must be between {MIN_PHOTO_YEAR} and {today.year}")
    try:
        if path.stat().st_size > OWN_PHOTO_MAX_BYTES:
            raise Refused(f"{path.name} is over {OWN_PHOTO_MAX_BYTES} bytes")
        image = process(path.read_bytes(), MODE_PHOTO)
    except (OSError, ImageError) as exc:
        raise Refused(f"{path.name}: {exc}") from exc
    return PhotoSource(
        image, OWN_PHOTO_AUTHOR, OWN_PHOTO_LICENSE, PHOTO_LICENSES[OWN_PHOTO_LICENSE][1],
        OWN_PHOTO_SOURCE_URL, SOURCE_OWN, year,
    )


def build_record(source: PhotoSource, alt_es: str, alt_en: str, today: dt.date) -> dict:
    """The entry for beach_photos.json, refused unless the dashboard would accept it."""
    digest = hashlib.sha256(source.image.data).hexdigest()
    record = {
        "file": f"{SUBDIR_BEACHES}/{digest}.webp",
        "width": source.image.width,
        "height": source.image.height,
        "bytes": len(source.image.data),
        "sha256": digest,
        "author": source.author,
        "license": source.license_code,
        "license_url": source.license_url,
        "source_url": source.source_url,
        "source": source.source,
        "taken_year": source.year,
        "alt": {"es": alt_es.strip(), "en": alt_en.strip()},
        "method": METHOD_CURATED,
        "reviewed_at": today.isoformat(),
    }
    problems = beach_photo_problems(record)
    if problems:
        raise Refused("; ".join(problems))
    return record


def _check_budget(static_dir: Path, new_bytes: int) -> None:
    used = sum(path.stat().st_size for path in static_dir.rglob("*") if path.is_file())
    if used + new_bytes > MAX_STATIC_BYTES:
        raise Refused(f"static images would be {used + new_bytes} bytes, over the {MAX_STATIC_BYTES} budget")


def _remove_replaced(old_entry: object, photos: dict, static_dir: Path) -> None:
    """Delete a replaced photo's file unless another beach uses it. Only a file name
    that matches the hash pattern can be deleted, whatever the JSON says.
    """
    file = old_entry.get("file") if isinstance(old_entry, dict) else None
    if not isinstance(file, str) or not BEACH_IMAGE_FILE_RE.fullmatch(file):
        return
    if all(not isinstance(entry, dict) or entry.get("file") != file for entry in photos.values()):
        (static_dir / file).unlink(missing_ok=True)


def run(args: argparse.Namespace, api: Api, today: dt.date) -> dict:
    """Do the import and return the new entry, or raise Refused with nothing written."""
    if args.beach not in {beach["name"] for beach in BEACHES}:
        raise Refused(f"unknown beach: {args.beach}")
    if not args.alt_es.strip() or not args.alt_en.strip():
        raise Refused("both --alt-es and --alt-en are required, written by a person")
    photos = _read_photos_strict(args.output)
    if args.beach in photos and not args.replace:
        raise Refused(f"{args.beach} already has a photo; pass --replace to change it")
    is_own = args.own is not None
    source = _from_own(args.own, args.year, today) if is_own else _from_commons(api, args.commons_file)
    record = build_record(source, args.alt_es, args.alt_en, today)
    _check_budget(args.static_dir, record["bytes"])
    old_entry = photos.get(args.beach)
    save(source.image.data, SUBDIR_BEACHES, args.static_dir)
    photos[args.beach] = record
    write_atomic(args.output, serialise(photos))
    _remove_replaced(old_entry, photos, args.static_dir)
    return record


def _manifest_text(entry: dict, key: str, label: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Refused(f"{label}: {key} is required")
    return value.strip()


def _manifest_alt(entry: dict, key: str, label: str) -> str:
    text = _manifest_text(entry, key, label)
    if FORBIDDEN_TEXT_RE.search(text) or len(text) > MAX_ALT_CHARS:
        raise Refused(f"{label}: {key} has markup or control characters, or is over {MAX_ALT_CHARS} characters")
    return text


def _manifest_entry(raw: object, number: int, args: argparse.Namespace, today: dt.date) -> argparse.Namespace:
    label = f"entry {number}"
    if not isinstance(raw, dict) or not MANIFEST_KEYS.issuperset(raw):
        raise Refused(f"{label}: must be an object with only {', '.join(sorted(MANIFEST_KEYS))}")
    beach = _manifest_text(raw, "beach", label)
    if beach not in {item["name"] for item in BEACHES}:
        raise Refused(f"{label}: unknown beach {beach!r}")
    alt_es, alt_en = _manifest_alt(raw, "alt_es", label), _manifest_alt(raw, "alt_en", label)
    commons_file, own, year = raw.get("commons_file"), raw.get("own"), raw.get("year")
    if (commons_file is None) == (own is None):
        raise Refused(f"{label} ({beach}): give exactly one of commons_file or own")
    if commons_file is not None:
        if year is not None or not isinstance(commons_file, str) or normalise_title(commons_file) is None:
            raise Refused(f'{label} ({beach}): commons_file must look like "File:Name.jpg", and year is only for own')
        own_path = None
    else:
        if not isinstance(own, str) or not own.strip() or isinstance(year, bool) or not isinstance(year, int):
            raise Refused(f"{label} ({beach}): own needs a path and a whole-number year")
        own_path = Path(own)
    return argparse.Namespace(
        beach=beach, commons_file=commons_file, own=own_path, year=year, alt_es=alt_es, alt_en=alt_en,
        replace=args.replace, output=args.output, static_dir=args.static_dir,
    )


def load_manifest(path: Path, args: argparse.Namespace, today: dt.date) -> list[argparse.Namespace]:
    """Read and check the whole manifest before anything is fetched or written."""
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise Refused(f"{path.name} is over {MAX_MANIFEST_BYTES} bytes")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"{path.name} can't be read as JSON: {exc}") from exc
    photos = payload.get("photos") if isinstance(payload, dict) and set(payload) == {"photos"} else None
    if not isinstance(photos, list) or not 1 <= len(photos) <= MAX_MANIFEST_PHOTOS:
        raise Refused(f'{path.name} must be {{"photos": [...]}} with 1 to {MAX_MANIFEST_PHOTOS} entries')
    entries = [_manifest_entry(raw, number, args, today) for number, raw in enumerate(photos, start=1)]
    names = [entry.beach for entry in entries]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise Refused(f"more than one photo for: {', '.join(repeated)}")
    existing = _read_photos_strict(args.output)
    present = [name for name in names if name in existing]
    if present and not args.replace:
        raise Refused(f"already have a photo: {', '.join(present)}; pass --replace to change them")
    return entries


class Outcome(NamedTuple):
    """The result for one manifest entry."""

    beach: str
    record: dict | None
    error: str | None


def run_manifest(entries: list[argparse.Namespace], api: Api, today: dt.date) -> list[Outcome]:
    """Import one entry at a time. A refusal affects only that beach, except HTTP 429,
    which stops the rest: more requests won't help.
    """
    outcomes: list[Outcome] = []
    stopped = False
    for entry in entries:
        if stopped:
            outcomes.append(Outcome(entry.beach, None, "not attempted: stopped after HTTP 429"))
            continue
        try:
            outcomes.append(Outcome(entry.beach, run(entry, api, today), None))
        except Refused as exc:
            stopped = exc.rate_limited
            outcomes.append(Outcome(entry.beach, None, str(exc)))
    return outcomes


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, help="import every photo listed in this JSON file (see the contact sheet)")
    parser.add_argument("--beach", help="exact name from BEACHES")
    origin = parser.add_mutually_exclusive_group()
    origin.add_argument("--commons-file", help='Commons title, such as "File:Name.jpg"')
    origin.add_argument("--own", type=Path, help="path to your own JPEG, PNG or WebP")
    parser.add_argument("--year", type=int, help="year taken; required with --own")
    parser.add_argument("--alt-es", help="Spanish alt text describing the scene")
    parser.add_argument("--alt-en", help="English alt text describing the scene")
    parser.add_argument("--replace", action="store_true", help="replace existing photos")
    parser.add_argument("--output", type=Path, default=BEACH_PHOTOS_PATH, help="file to write")
    parser.add_argument("--static-dir", type=Path, default=STATIC_DIR, help="folder for image files")
    args = parser.parse_args(argv)
    single = ("beach", "commons_file", "own", "year", "alt_es", "alt_en")
    if args.manifest is not None:
        if any(getattr(args, key) is not None for key in single):
            parser.error("--manifest can't be combined with the single-photo options")
        return args
    for key in ("beach", "alt_es", "alt_en"):
        if getattr(args, key) is None:
            parser.error(f"--{key.replace('_', '-')} is required")
    if args.commons_file is None and args.own is None:
        parser.error("give --commons-file or --own")
    if args.own is not None and args.year is None:
        parser.error("--own needs --year")
    if args.commons_file is not None and args.year is not None:
        parser.error("--year is only for --own: a Commons year comes from the source")
    return args


def _describe(record: dict) -> list[str]:
    return [
        f"{record['file']} ({record['width']}x{record['height']}, {record['bytes']} bytes)",
        f"  Credit: {record['author']}, {record['license']}, year {record['taken_year']}",
        f"  Source: {record['source_url']}",
    ]


def _main_manifest(args: argparse.Namespace, transport: httpx.BaseTransport | None, sleep: Callable[[float], None]) -> int:
    today = dt.date.today()
    try:
        entries = load_manifest(args.manifest, args, today)
    except Refused as exc:
        print(f"Refused, nothing written: {exc}", file=sys.stderr)
        return 1
    with make_client(transport) as client:
        outcomes = run_manifest(entries, Api(client, sleep), today)
    for outcome in outcomes:
        if outcome.record:
            print(f"Imported {outcome.beach}: " + "\n".join(_describe(outcome.record)))
        else:
            print(f"FAILED {outcome.beach}: {outcome.error}", file=sys.stderr)
    done = sum(1 for outcome in outcomes if outcome.record)
    print(f"{done} of {len(outcomes)} imported.")
    if done:
        print(CHECKLIST)
    return 0 if done == len(outcomes) else 1


def main(
    argv: list[str] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    args = _parse_args(argv)
    if args.manifest is not None:
        return _main_manifest(args, transport, sleep)
    try:
        with make_client(transport) as client:
            record = run(args, Api(client, sleep), dt.date.today())
    except Refused as exc:
        print(f"Refused, nothing written: {exc}", file=sys.stderr)
        return 1
    print(f"Imported {args.beach}: " + "\n".join(_describe(record)))
    print(CHECKLIST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
