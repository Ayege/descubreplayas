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

"""Offline tests for the self-hosted images and their contract.

Every check runs over the shipped files, and also over generated images, so the
checks are proven to fail even while dashboard/static/ is empty. Run from the
repo root:

    python -m tests.test_images
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import sys
import tempfile
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from unittest import mock

from PIL import Image

from dashboard import conoce_data as cd
from tests.conoce_fixtures import BEACH, loc, photo, taxon

_REPO = Path(__file__).resolve().parent.parent
_IGNORED_NAMES = frozenset({".gitkeep", ".DS_Store"})
_STATIC_SUBDIRS = ("beaches", "species")


def _check(condition: bool, message: str) -> None:
    assert condition, message


def _metadata_problems(path: Path) -> list[str]:
    with Image.open(path) as image:
        if image.getexif():
            return [f"{path.name}: has EXIF"]
        keys = {str(key).lower() for key in image.info}
    found = keys & {"exif", "xmp", "icc_profile"}
    return [f"{path.name}: has {sorted(found)}"] if found else []


def _name_problems(paths: Iterable[Path]) -> list[str]:
    return [f"{p.name}: not a hash file name" for p in paths if not cd.IMAGE_NAME_RE.fullmatch(p.name)]


def _file_problems(
    static_dir: Path,
    relative: str,
    expected_hash: str,
    max_bytes: int,
    size_rule: tuple[int, int] | None,
) -> list[str]:
    """Check one referenced file against its entry and the budgets."""
    path = static_dir / relative
    if not path.is_file():
        return [f"{relative}: missing"]
    problems: list[str] = []
    if cd.file_sha256(path) != expected_hash:
        problems.append(f"{relative}: sha256 does not match")
    if path.stat().st_size > max_bytes:
        problems.append(f"{relative}: over {max_bytes} bytes")
    with Image.open(path) as image:
        width, height = image.size
    if size_rule and (width, height) != size_rule:
        problems.append(f"{relative}: is {width}x{height}, expected {size_rule}")
    if width > cd.MAX_BEACH_PHOTO_WIDTH:
        problems.append(f"{relative}: wider than {cd.MAX_BEACH_PHOTO_WIDTH}")
    return [*problems, *_metadata_problems(path)]


def audit_images(static_dir: Path, photos: Mapping, taxa: Mapping) -> list[str]:
    """Every problem with the images on disk and the entries that reference them."""
    problems: list[str] = []
    referenced: set[str] = set()
    for beach, entry in photos.items():
        problems += [f"{beach}: {p}" for p in cd.beach_photo_problems(entry)]
        if isinstance(entry, Mapping) and isinstance(entry.get("file"), str) and cd.BEACH_IMAGE_FILE_RE.fullmatch(entry["file"]):
            referenced.add(entry["file"])
            problems += _file_problems(static_dir, entry["file"], entry.get("sha256"), cd.MAX_BEACH_PHOTO_BYTES,
                                       (entry.get("width"), entry.get("height")))
    for key, record in taxa.items():
        species_photo = record.get("photo") if isinstance(record, Mapping) else None
        if not isinstance(species_photo, Mapping) or "file" not in species_photo:
            continue
        problems += [f"taxon {key}: {p}" for p in cd.photo_problems(species_photo)]
        if isinstance(species_photo["file"], str) and cd.SPECIES_IMAGE_FILE_RE.fullmatch(species_photo["file"]):
            referenced.add(species_photo["file"])
            problems += _file_problems(static_dir, species_photo["file"], species_photo.get("sha256"), cd.MAX_THUMBNAIL_BYTES,
                                       (cd.THUMBNAIL_FILE_PX, cd.THUMBNAIL_FILE_PX))
    on_disk = [p for p in static_dir.rglob("*") if p.is_file() and p.name not in _IGNORED_NAMES]
    problems += _name_problems(on_disk)
    problems += [f"{p.relative_to(static_dir)}: orphan" for p in on_disk if p.relative_to(static_dir).as_posix() not in referenced]
    if sum(p.stat().st_size for p in on_disk) > cd.MAX_STATIC_BYTES:
        problems.append(f"static folder is over {cd.MAX_STATIC_BYTES} bytes")
    return problems


def _webp(width: int = 480, height: int = 320, **save) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (0, 120, 160)).save(buffer, "WEBP", **save)
    return buffer.getvalue()


def _write(static_dir: Path, subdir: str, data: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(data).hexdigest()
    (static_dir / subdir).mkdir(parents=True, exist_ok=True)
    (static_dir / subdir / f"{digest}.webp").write_bytes(data)
    return f"{subdir}/{digest}.webp", digest


def _beach_entry(relative: str, digest: str, data: bytes, width: int = 480, height: int = 320) -> dict:
    return {
        "file": relative,
        "width": width,
        "height": height,
        "bytes": len(data),
        "sha256": digest,
        "author": "Persona de prueba",
        "license": "cc-by",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:Prueba.jpg",
        "source": "wikimedia_commons",
        "taken_year": None,
        "alt": loc("Playa de arena con palmeras", "Sandy beach with palm trees"),
        "method": "curated",
        "reviewed_at": "2026-11-04",
    }


def _valid_tree(folder: Path) -> tuple[dict, dict]:
    """A static folder with one beach photo and one thumbnail, both valid."""
    beach_data, thumb_data = _webp(), _webp(96, 96)
    relative, digest = _write(folder, "beaches", beach_data)
    thumb_file, thumb_hash = _write(folder, "species", thumb_data)
    record = taxon(1)
    record["photo"].update(file=thumb_file, width=96, height=96, sha256=thumb_hash)
    return {BEACH: _beach_entry(relative, digest, beach_data)}, {"1": record}


def test_shipped_images_pass() -> None:
    photos = json.loads(cd.BEACH_PHOTOS_PATH.read_text(encoding="utf-8"))
    taxa = json.loads(cd.AUTO_SPECIES_PATH.read_text(encoding="utf-8"))["taxa"]
    problems = audit_images(cd.STATIC_DIR, photos, taxa)
    _check(problems == [], f"shipped images: {problems}")


def test_budget_constants_match_the_brief() -> None:
    actual = (cd.MAX_BEACH_PHOTO_WIDTH, cd.MAX_BEACH_PHOTO_BYTES, cd.THUMBNAIL_FILE_PX, cd.MAX_THUMBNAIL_BYTES, cd.MAX_STATIC_BYTES, cd.MAX_ALT_CHARS)
    _check(actual == (480, 45 * 1024, 96, 6 * 1024, 3 * 1024 * 1024, 125), "budgets")


def test_valid_tree_passes() -> None:
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        photos, taxa = _valid_tree(folder)
        _check(audit_images(folder, photos, taxa) == [], f"{audit_images(folder, photos, taxa)}")


def _audit_after(change) -> list[str]:
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        photos, taxa = _valid_tree(folder)
        change(folder, photos, taxa)
        return audit_images(folder, photos, taxa)


def _beach_path(folder: Path, photos: dict) -> Path:
    return folder / photos[BEACH]["file"]


def _set_entry(field: str, value: object):
    return lambda folder, photos, taxa: photos[BEACH].__setitem__(field, value)


_BAD_TREES = {
    "missing file": lambda f, p, t: _beach_path(f, p).unlink(),
    "changed file": lambda f, p, t: _beach_path(f, p).write_bytes(_webp(480, 300)),
    "orphan image": lambda f, p, t: _write(f, "beaches", _webp(100, 100)),
    "orphan thumbnail": lambda f, p, t: _write(f, "species", _webp(96, 96, quality=10)),
    "non-hash name": lambda f, p, t: (f / "beaches" / "playa_bavaro.webp").write_bytes(_webp()),
    "traversal name": lambda f, p, t: _set_entry("file", "../../secrets.webp")(f, p, t),
    "disallowed license": _set_entry("license", "cc-by-nc"),
    "no author": _set_entry("author", ""),
    "missing spanish alt": _set_entry("alt", {"en": "x"}),
    "empty alt": _set_entry("alt", loc("  ", "x")),
    "alt too long": _set_entry("alt", loc("x" * 126, "y")),
    "unsafe source": _set_entry("source_url", "javascript:alert(1)"),
    "bad source kind": _set_entry("source", "instagram"),
    "guessed future year": _set_entry("taken_year", 9999),
    "generated method": _set_entry("method", "api:inaturalist"),
    "no review date": _set_entry("reviewed_at", "pronto"),
    "claimed size wrong": _set_entry("width", 300),
    "claimed too wide": _set_entry("width", 900),
    "claimed too heavy": _set_entry("bytes", 46 * 1024),
    "extra coordinates": _set_entry("latitude", 18.5),
}


def test_bad_trees_are_caught() -> None:
    for label, change in _BAD_TREES.items():
        _check(_audit_after(change) != [], f"should be caught: {label}")


def test_oversize_files_are_caught() -> None:
    def too_wide(folder: Path, photos: dict, taxa: dict) -> None:
        data = _webp(900, 300)
        relative, digest = _write(folder, "beaches", data)
        _beach_path(folder, photos).unlink()
        photos[BEACH] = _beach_entry(relative, digest, data, 900, 300)
        photos[BEACH]["width"] = 480

    _check(_audit_after(too_wide) != [], "an image wider than 480 px must be caught")

    def wrong_thumb(folder: Path, photos: dict, taxa: dict) -> None:
        data = _webp(75, 75)
        relative, digest = _write(folder, "species", data)
        taxa["1"]["photo"].update(file=relative, sha256=digest)

    problems = _audit_after(wrong_thumb)
    _check(any("expected (96, 96)" in p for p in problems), f"a thumbnail must be 96x96: {problems}")
    with mock.patch.object(cd, "MAX_STATIC_BYTES", 10):
        _check(any("static folder" in p for p in _audit_after(lambda f, p, t: None)), "the folder budget must be enforced")
    with mock.patch.object(cd, "MAX_THUMBNAIL_BYTES", 1):
        _check(_audit_after(lambda f, p, t: None) != [], "the thumbnail budget must be enforced")


def test_metadata_is_caught() -> None:
    exif = Image.Exif()
    exif[0x010E] = "Punta Cana"
    for label, save in (("exif", {"exif": exif}), ("xmp", {"xmp": b"<x:xmpmeta/>"})):
        def carrying(folder: Path, photos: dict, taxa: dict, save: dict = save) -> None:
            data = _webp(**save)
            relative, digest = _write(folder, "beaches", data)
            _beach_path(folder, photos).unlink()
            photos[BEACH] = _beach_entry(relative, digest, data)

        problems = _audit_after(carrying)
        _check(any("has" in p for p in problems), f"{label} metadata must be caught: {problems}")


def test_thumbnail_contract() -> None:
    plain = photo()
    _check(cd.photo_problems(plain) == [], "a photo without a local copy is still valid until F2")
    partial = dict(plain, file="species/" + "a" * 64 + ".webp")
    _check(cd.photo_problems(partial) != [], "file, width, height and sha256 go together")
    good = dict(plain, file="species/" + "a" * 64 + ".webp", width=96, height=96, sha256="a" * 64)
    _check(cd.photo_problems(good) == [], "a complete copy is valid")
    for label, change in (
        ("wrong hash", {"sha256": "b" * 64}),
        ("traversal", {"file": "../" + "a" * 64 + ".webp"}),
        ("beach folder", {"file": "beaches/" + "a" * 64 + ".webp"}),
        ("not text", {"file": 5}),
        ("wrong size", {"width": 75}),
    ):
        _check(cd.photo_problems({**good, **change}) != [], f"should be rejected: {label}")


def test_beach_file_contract() -> None:
    data = _webp()
    digest = hashlib.sha256(data).hexdigest()
    good = _beach_entry(f"beaches/{digest}.webp", digest, data)
    _check(cd.beach_photo_problems(good) == [], "a content-addressed path is valid")
    for label, path in (
        ("traversal", f"../../{digest}.webp"),
        ("nested traversal", f"beaches/../../{digest}.webp"),
        ("absolute", f"/etc/{digest}.webp"),
        ("species folder", f"species/{digest}.webp"),
        ("wrong extension", f"beaches/{digest}.jpg"),
        ("other hash", "beaches/" + "a" * 64 + ".webp"),
        ("data-derived name", "beaches/playa_bavaro.webp"),
        ("not text", 5),
    ):
        problems = cd.beach_photo_problems({**good, "file": path})
        _check(any("beaches/<sha256>.webp" in p for p in problems), f"path should be rejected: {label}")
    outside = {BEACH: {**good, "file": f"../{digest}.webp"}}
    with tempfile.TemporaryDirectory() as name, mock.patch.object(cd, "file_sha256") as reader:
        index = cd.build_index({}, {}, {}, photos=outside, static_dir=Path(name))
    _check(index[BEACH]["photo"] is None and not reader.called, "a rejected path is never opened")


def test_loader_returns_a_good_photo() -> None:
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        photos, _ = _valid_tree(folder)
        index = cd.build_index({}, {}, {}, photos=photos, static_dir=folder)
    _check(index[BEACH]["photo"]["author"] == "Persona de prueba", "the photo should load")


def test_loader_skips_bad_photos_and_logs_once() -> None:
    def build(change) -> tuple[dict, int]:
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            photos, _ = _valid_tree(folder)
            change(folder, photos)
            with mock.patch.object(cd.logger, "warning") as warning:
                index = cd.build_index({}, {}, {}, photos=photos, static_dir=folder)
        return index, warning.call_count

    cases = {
        "missing file": lambda f, p: _beach_path(f, p).unlink(),
        "changed file": lambda f, p: _beach_path(f, p).write_bytes(b"not the same"),
        "bad entry": lambda f, p: p[BEACH].__setitem__("license", "cc-by-nd"),
        "not an object": lambda f, p: p.__setitem__(BEACH, 5),
    }
    for label, change in cases.items():
        index, warnings = build(change)
        _check(index[BEACH]["photo"] is None, f"should be skipped: {label}")
        _check(warnings == 1, f"{label}: logged {warnings} times")


def test_public_function_and_cache() -> None:
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        photos, _ = _valid_tree(folder)
        fake = cd.build_index({}, {}, {}, photos=photos, static_dir=folder)
    with mock.patch.object(cd, "load_index", return_value=fake):
        _check(cd.beach_photo(BEACH)["sha256"] == photos[BEACH]["sha256"], "beach_photo")
        _check(cd.beach_photo("Playa inexistente") is None, "unknown beach")
    cd.load_index.cache_clear()
    with mock.patch.object(cd, "read_beach_photos", wraps=cd.read_beach_photos) as reader:
        for _ in range(100):
            cd.beach_photo(BEACH)
    _check(reader.call_count == 1, "the photo file is read once per process")
    cd.load_index.cache_clear()


def test_read_beach_photos_failures() -> None:
    with tempfile.TemporaryDirectory() as name:
        path = Path(name) / "photos.json"
        _check(cd.read_beach_photos(path) == {}, "a missing file reads as empty")
        for body in ("{not json", "[]", "5"):
            path.write_text(body, encoding="utf-8")
            _check(cd.read_beach_photos(path) == {}, f"should read as empty: {body}")


def test_serving_is_enabled_for_every_deployment() -> None:
    config = tomllib.loads((_REPO / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    _check(config["server"]["enableStaticServing"] is True, "local and Community Cloud config")
    docker = (_REPO / "Dockerfile.dashboard").read_text(encoding="utf-8")
    _check("enableStaticServing = true" in docker, "Cloud Run writes its own config")
    _check("COPY dashboard/ dashboard/" in docker, "the image must include dashboard/static/")


def test_tested_versions_are_pinned() -> None:
    pins = (_REPO / "requirements.txt").read_text(encoding="utf-8").splitlines()
    for line in ("streamlit==1.58.0", "starlette==1.3.1", "uvicorn[standard]==0.49.0", "pillow==12.2.0"):
        _check(line in pins, f"requirements.txt must pin {line}: static files fail on other combinations")


def test_static_folder_sits_beside_the_main_script() -> None:
    _check(cd.STATIC_DIR == _REPO / "dashboard" / "static", "Streamlit serves ./static beside the main script")
    _check((_REPO / "dashboard" / "beaches.py").is_file(), "the main script")
    for subdir in _STATIC_SUBDIRS:
        _check((cd.STATIC_DIR / subdir).is_dir(), f"static/{subdir} must exist")


def main() -> int:
    logging.disable(logging.CRITICAL)
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    for name, test in tests:
        test()
        print(f"  ok {name}")
    print(f"All {len(tests)} image tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
