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

"""Download, re-encode and store images, for every script that handles them.

Everything from the network is untrusted: only allowlisted hosts, only JPEG, PNG and
WebP, a size cap, and every image decoded and re-encoded so no metadata survives. File
names come from the hash of our own output, never from data.

Needs Pillow 13 (requirements/ingest.lock), which can't share an environment with
Streamlit 1.58. See requirements/README.md.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

import PIL
from PIL import Image, ImageOps, UnidentifiedImageError

from dashboard.conoce_data import (
    IMAGE_NAME_RE,
    MAX_BEACH_PHOTO_BYTES,
    MAX_BEACH_PHOTO_WIDTH,
    MAX_THUMBNAIL_BYTES,
    THUMBNAIL_FILE_PX,
    is_safe_url,
)
from scripts._http import RETRY_STATUSES, Api, get_bytes

MIN_PILLOW_MAJOR = 13
if int(PIL.__version__.split(".")[0]) < MIN_PILLOW_MAJOR:
    raise ImportError(
        f"scripts/_images.py needs Pillow {MIN_PILLOW_MAJOR}, found {PIL.__version__}. "
        "Run it with .venv-ingest: see requirements/README.md."
    )

MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
# Longer than MAX_URL_CHARS, which is for URLs we store. Download URLs aren't stored,
# and a Commons thumbnail URL contains the file name twice.
MAX_DOWNLOAD_URL_CHARS = 1000
MAX_IMAGE_PIXELS = 25_000_000
DOWNLOAD_DELAY_S = 0.2

MODE_THUMB = "thumb"
MODE_PHOTO = "photo"
SUBDIR_SPECIES = "species"
SUBDIR_BEACHES = "beaches"
_SUBDIRS = frozenset({SUBDIR_SPECIES, SUBDIR_BEACHES})

THUMBNAIL_QUALITY = 70
PHOTO_START_QUALITY = 85
PHOTO_MIN_QUALITY = 50
PHOTO_QUALITY_STEP = 5
_WEBP_METHOD = 6
# Temp files start private, but a stored image has to be readable by the web server.
_STORED_FILE_MODE = 0o644

# Only these formats can be decoded, which keeps the other decoders out of reach.
_PILLOW_FORMATS = ("JPEG", "PNG", "WEBP")
_ALLOWED_CONTENT_TYPES = {"image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp"}
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SMALL_FROM_SQUARE_RE = re.compile(r"/square\.(jpe?g|png)(\?.*)?$", re.IGNORECASE)


class ImageError(Exception):
    """An image was refused, or could not be processed."""


class Processed(NamedTuple):
    """A re-encoded image, ready to store."""

    data: bytes
    width: int
    height: int


def small_url_for(square_url: str) -> str:
    """The 240 px version of an iNaturalist square URL."""
    url, count = _SMALL_FROM_SQUARE_RE.subn(lambda m: f"/small.{m.group(1)}", square_url)
    if count != 1:
        raise ImageError("not a square photo URL")
    return url


def _kind_from_magic(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(_PNG_MAGIC):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _check_host(url: str, hosts: Collection[str]) -> None:
    if not is_safe_url(url, MAX_DOWNLOAD_URL_CHARS):
        raise ImageError("unsafe image URL")
    parts = urlsplit(url)
    if parts.hostname not in hosts or parts.port not in (None, 443):
        raise ImageError(f"host not on the download allowlist: {parts.hostname}")


def download(
    api: Api,
    url: str,
    hosts: Collection[str],
    retry_statuses: frozenset[int] = RETRY_STATUSES,
) -> bytes:
    """Fetch an image from an allowlisted host. The HTTP client follows no redirects,
    so a redirect to another host is refused rather than followed.
    """
    _check_host(url, hosts)
    data, content_type = get_bytes(api, url, DOWNLOAD_DELAY_S, MAX_DOWNLOAD_BYTES, retry_statuses)
    declared = _ALLOWED_CONTENT_TYPES.get(content_type.split(";")[0].strip().lower())
    if declared is None:
        raise ImageError(f"Content-Type not allowed: {content_type[:40]}")
    if _kind_from_magic(data) != declared:
        raise ImageError("the file's first bytes don't match its Content-Type")
    return data


def _decode(raw: bytes) -> Image.Image:
    """Decode to plain RGB with its orientation applied, and nothing else kept."""
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(io.BytesIO(raw), formats=_PILLOW_FORMATS) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ImageError("image has too many pixels")
            oriented = ImageOps.exif_transpose(image)
            return oriented.convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError) as exc:
        raise ImageError(f"could not decode image: {type(exc).__name__}") from exc


def _encode(image: Image.Image, quality: int) -> bytes:
    clean = Image.new("RGB", image.size)
    clean.paste(image)
    buffer = io.BytesIO()
    clean.save(buffer, "WEBP", quality=quality, method=_WEBP_METHOD)
    return buffer.getvalue()


def _thumbnail(image: Image.Image) -> Processed:
    side = min(image.size)
    left, top = (image.width - side) // 2, (image.height - side) // 2
    square = image.crop((left, top, left + side, top + side))
    resized = square.resize((THUMBNAIL_FILE_PX, THUMBNAIL_FILE_PX), Image.Resampling.LANCZOS)
    data = _encode(resized, THUMBNAIL_QUALITY)
    if len(data) > MAX_THUMBNAIL_BYTES:
        raise ImageError(f"thumbnail is {len(data)} bytes, over the {MAX_THUMBNAIL_BYTES} budget")
    return Processed(data, THUMBNAIL_FILE_PX, THUMBNAIL_FILE_PX)


def _photo(image: Image.Image) -> Processed:
    if image.width > MAX_BEACH_PHOTO_WIDTH:
        height = round(image.height * MAX_BEACH_PHOTO_WIDTH / image.width)
        image = image.resize((MAX_BEACH_PHOTO_WIDTH, height), Image.Resampling.LANCZOS)
    for quality in range(PHOTO_START_QUALITY, PHOTO_MIN_QUALITY - 1, -PHOTO_QUALITY_STEP):
        data = _encode(image, quality)
        if len(data) <= MAX_BEACH_PHOTO_BYTES:
            return Processed(data, image.width, image.height)
    raise ImageError(f"photo doesn't fit {MAX_BEACH_PHOTO_BYTES} bytes at quality {PHOTO_MIN_QUALITY}")


def process(raw: bytes, mode: str) -> Processed:
    """Decode and re-encode as WebP with no metadata, as a thumbnail or a photo."""
    image = _decode(raw)
    if mode == MODE_THUMB:
        return _thumbnail(image)
    if mode == MODE_PHOTO:
        return _photo(image)
    raise ValueError(f"unknown mode: {mode}")


def save(data: bytes, subdir: str, static_dir: Path) -> tuple[str, str]:
    """Store bytes under the hash of their content, atomically. Identical images are
    stored once. Returns the path relative to static_dir, and the hash.
    """
    if subdir not in _SUBDIRS:
        raise ValueError(f"unknown image folder: {subdir}")
    digest = hashlib.sha256(data).hexdigest()
    folder = static_dir / subdir
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.webp"
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=folder, suffix=".tmp", delete=False) as handle:
            handle.write(data)
        try:
            os.chmod(handle.name, _STORED_FILE_MODE)
            os.replace(handle.name, path)
        except OSError:
            Path(handle.name).unlink(missing_ok=True)
            raise
    return f"{subdir}/{digest}.webp", digest


def remove_orphans(static_dir: Path, referenced: Iterable[str], dry_run: bool) -> list[str]:
    """Delete species thumbnails nothing references. Only files directly inside
    static/species/ whose names match the hash pattern; never anything else.
    """
    keep = set(referenced)
    folder = static_dir / SUBDIR_SPECIES
    if not folder.is_dir() or folder.is_symlink():
        return []
    removed: list[str] = []
    for path in sorted(folder.iterdir()):
        relative = f"{SUBDIR_SPECIES}/{path.name}"
        if path.is_symlink() or not path.is_file() or not IMAGE_NAME_RE.fullmatch(path.name) or relative in keep:
            continue
        removed.append(relative)
        if not dry_run:
            path.unlink()
    return removed
