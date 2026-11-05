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
        raise Refused(f"HTTP 429, wrong size or rate limited; not retrying: {exc}") from exc
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


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--beach", required=True, help="exact name from BEACHES")
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--commons-file", help='Commons title, such as "File:Name.jpg"')
    origin.add_argument("--own", type=Path, help="path to your own JPEG, PNG or WebP")
    parser.add_argument("--year", type=int, help="year taken; required with --own")
    parser.add_argument("--alt-es", required=True, help="Spanish alt text describing the scene")
    parser.add_argument("--alt-en", required=True, help="English alt text describing the scene")
    parser.add_argument("--replace", action="store_true", help="replace this beach's existing photo")
    parser.add_argument("--output", type=Path, default=BEACH_PHOTOS_PATH, help="file to write")
    parser.add_argument("--static-dir", type=Path, default=STATIC_DIR, help="folder for image files")
    args = parser.parse_args(argv)
    if args.own is not None and args.year is None:
        parser.error("--own needs --year")
    if args.commons_file is not None and args.year is not None:
        parser.error("--year is only for --own: a Commons year comes from the source")
    return args


def main(
    argv: list[str] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    args = _parse_args(argv)
    try:
        with make_client(transport) as client:
            record = run(args, Api(client, sleep), dt.date.today())
    except Refused as exc:
        print(f"Refused, nothing written: {exc}", file=sys.stderr)
        return 1
    print(f"Imported {args.beach}: {record['file']} ({record['width']}x{record['height']}, {record['bytes']} bytes)")
    print(f"Credit: {record['author']}, {record['license']}, year {record['taken_year']}")
    print(f"Source: {record['source_url']}")
    print(CHECKLIST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
