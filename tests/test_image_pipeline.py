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

"""Offline tests for scripts/_images.py. Fixture images are generated here.

Needs Pillow 13, so run it with the ingest environment, from the repo root:

    .venv-ingest/bin/python -m tests.test_image_pipeline
"""
from __future__ import annotations

import hashlib
import io
import logging
import struct
import sys
import tempfile
import zlib
from pathlib import Path
from unittest import mock

import httpx
from PIL import ExifTags, Image

from scripts import _images as img
from scripts._http import Api, FetchError, make_client

_HOST = "inaturalist-open-data.s3.amazonaws.com"
_URL = f"https://{_HOST}/photos/1/small.jpg"
_HOSTS = frozenset({_HOST})


def _check(condition: bool, message: str) -> None:
    assert condition, message


def _raises(kind: type, call, message: str) -> None:
    try:
        call()
    except kind:
        return
    raise AssertionError(message)


def _image(size: tuple[int, int] = (240, 160), fmt: str = "JPEG", **save) -> bytes:
    pixels = Image.effect_noise(size, 40).convert("RGB")
    buffer = io.BytesIO()
    pixels.save(buffer, fmt, **save)
    return buffer.getvalue()


def _gps_jpeg() -> bytes:
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Camara de prueba"
    exif[ExifTags.Base.Orientation] = 6
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (18.0, 40.0, 0.0)
    return _image(exif=exif)


def _bomb_png() -> bytes:
    """A tiny, valid-looking PNG that declares huge dimensions."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", 50_000, 50_000, 8, 2, 0, 0, 0)
    return img._PNG_MAGIC + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\0" * 16)) + chunk(b"IEND", b"")


def _api(handler) -> Api:
    return Api(make_client(httpx.MockTransport(handler)), sleep=lambda _: None)


def _serving(body: bytes, content_type: str = "image/jpeg", status: int = 200):
    return lambda _: httpx.Response(status, content=body, headers={"Content-Type": content_type})


def test_a_good_download_passes() -> None:
    body = _image()
    _check(img.download(_api(_serving(body)), _URL, _HOSTS) == body, "a valid JPEG should pass")
    png = _image(fmt="PNG")
    _check(img.download(_api(_serving(png, "image/png")), _URL, _HOSTS) == png, "a valid PNG should pass")
    webp = _image(fmt="WEBP")
    _check(img.download(_api(_serving(webp, "image/webp; x=1")), _URL, _HOSTS) == webp, "a valid WebP should pass")


def test_oversize_download_is_aborted() -> None:
    huge = b"\xff\xd8\xff" + b"x" * (img.MAX_DOWNLOAD_BYTES + 1)
    _raises(FetchError, lambda: img.download(_api(_serving(huge)), _URL, _HOSTS), "an oversize download must abort")


def test_wrong_content_type_is_rejected() -> None:
    for content_type in ("text/html", "image/svg+xml", "image/tiff", "application/octet-stream", ""):
        api = _api(_serving(_image(), content_type))
        _raises(img.ImageError, lambda: img.download(api, _URL, _HOSTS), f"should be rejected: {content_type!r}")


def test_wrong_type_with_unrecognised_bytes_is_rejected() -> None:
    api = _api(_serving(b"<html><script>x</script>", "text/html"))
    _raises(img.ImageError, lambda: img.download(api, _URL, _HOSTS), "unknown bytes must not match an unknown type")


def test_wrong_magic_bytes_are_rejected() -> None:
    for label, body in (("html", b"<html><script>x</script>"), ("png as jpeg", _image(fmt="PNG")), ("empty", b"")):
        api = _api(_serving(body))
        _raises(img.ImageError, lambda: img.download(api, _URL, _HOSTS), f"should be rejected: {label}")


def test_only_allowlisted_hosts_are_contacted() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=_image(), headers={"Content-Type": "image/jpeg"})

    bad = (
        "https://static.inaturalist.org/photos/1/small.jpg",
        f"https://{_HOST}.evil.test/photos/1/small.jpg",
        f"http://{_HOST}/photos/1/small.jpg",
        f"https://{_HOST}:8443/photos/1/small.jpg",
        f"https://user@{_HOST}/photos/1/small.jpg",
        "javascript:alert(1)",
    )
    for url in bad:
        _raises(img.ImageError, lambda url=url: img.download(_api(handler), url, _HOSTS), f"should be refused: {url}")
    _check(calls == [], "a refused URL must never be requested")


def test_redirects_are_refused() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.test/x.jpg"})

    _raises(FetchError, lambda: img.download(_api(handler), _URL, _HOSTS), "a redirect must be refused")
    _check(len(calls) == 1, "the redirect must not be followed")


def test_small_url() -> None:
    base = f"https://{_HOST}/photos/5/"
    _check(img.small_url_for(base + "square.jpg") == base + "small.jpg", "jpg")
    _check(img.small_url_for(base + "square.JPEG") == base + "small.JPEG", "jpeg")
    _check(img.small_url_for(base + "square.png?1") == base + "small.png", "query dropped")
    _raises(img.ImageError, lambda: img.small_url_for(base + "large.jpg"), "only square URLs")


def test_decompression_bomb_is_rejected() -> None:
    _raises(img.ImageError, lambda: img.process(_bomb_png(), img.MODE_THUMB), "a bomb must be rejected")


def test_too_many_pixels_is_rejected_below_the_bomb_threshold() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (6000, 5000)).save(buffer, "PNG")  # 30 MP: a real image, just over the limit
    _raises(img.ImageError, lambda: img.process(buffer.getvalue(), img.MODE_THUMB), "over the pixel limit")


def test_other_formats_cannot_be_decoded() -> None:
    for fmt in ("TIFF", "GIF", "BMP"):
        data = _image(fmt=fmt)
        _raises(img.ImageError, lambda data=data: img.process(data, img.MODE_THUMB), f"{fmt} must not be decoded")
    for junk in (b"", b"not an image", b"\xff\xd8\xff" + b"junk" * 20):
        _raises(img.ImageError, lambda junk=junk: img.process(junk, img.MODE_THUMB), "junk must be refused")


def _metadata(data: bytes) -> tuple[dict, dict]:
    with Image.open(io.BytesIO(data)) as image:
        return dict(image.getexif()), dict(image.info)


def test_metadata_is_stripped() -> None:
    raw = _gps_jpeg()
    _check(_metadata(raw)[0] != {}, "the fixture must really carry EXIF")
    for mode in (img.MODE_THUMB, img.MODE_PHOTO):
        exif, info = _metadata(img.process(raw, mode).data)
        _check(exif == {}, f"{mode}: EXIF survived")
        _check(not {"exif", "xmp", "icc_profile"} & set(info), f"{mode}: metadata survived: {set(info)}")
    xmp = _image(fmt="WEBP", xmp=b"<x:xmpmeta/>", exif=Image.Exif())
    _check("xmp" in _metadata(xmp)[1], "the fixture must really carry XMP")
    _check("xmp" not in _metadata(img.process(xmp, img.MODE_THUMB).data)[1], "XMP survived")


def test_orientation_is_applied() -> None:
    wide = _gps_jpeg()  # 240x160 with orientation 6: really 160x240
    result = img.process(wide, img.MODE_PHOTO)
    _check((result.width, result.height) == (160, 240), f"orientation ignored: {result.width}x{result.height}")


def test_thumbnail_shape() -> None:
    result = img.process(_image((240, 160)), img.MODE_THUMB)
    with Image.open(io.BytesIO(result.data)) as image:
        _check(image.format == "WEBP" and image.size == (96, 96), f"got {image.format} {image.size}")
    _check((result.width, result.height) == (96, 96), "reported size")
    _check(len(result.data) <= img.MAX_THUMBNAIL_BYTES, "thumbnail budget")
    with mock.patch.object(img, "MAX_THUMBNAIL_BYTES", 10):
        _raises(img.ImageError, lambda: img.process(_image(), img.MODE_THUMB), "over budget must fail")


def test_centre_crop_keeps_the_middle() -> None:
    source = Image.new("RGB", (300, 100), (255, 0, 0))
    source.paste((0, 0, 255), (100, 0, 200, 100))
    buffer = io.BytesIO()
    source.save(buffer, "PNG")
    with Image.open(io.BytesIO(img.process(buffer.getvalue(), img.MODE_THUMB).data)) as image:
        red, _green, blue = image.convert("RGB").getpixel((48, 48))
    _check(blue > 200 and red < 60, f"the crop should be the blue middle: {(red, blue)}")


def test_photo_mode_resizes_and_fits() -> None:
    result = img.process(_image((1600, 1000)), img.MODE_PHOTO)
    _check(result.width == img.MAX_BEACH_PHOTO_WIDTH and result.height == 300, f"{result.width}x{result.height}")
    _check(len(result.data) <= img.MAX_BEACH_PHOTO_BYTES, "photo budget")
    small = img.process(_image((300, 200)), img.MODE_PHOTO)
    _check(small.width == 300, "photos are never upscaled")
    with mock.patch.object(img, "MAX_BEACH_PHOTO_BYTES", 100):
        _raises(img.ImageError, lambda: img.process(_image((1600, 1000)), img.MODE_PHOTO), "must fail, not ship a mush")


def test_same_input_same_file_name() -> None:
    raw = _image()
    first, second = img.process(raw, img.MODE_THUMB).data, img.process(raw, img.MODE_THUMB).data
    _check(first == second, "the same input must give the same bytes")
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        a = img.save(first, img.SUBDIR_SPECIES, folder)
        b = img.save(second, img.SUBDIR_SPECIES, folder)
        _check(a == b, "the same bytes must give the same file")
        _check(a == (f"species/{hashlib.sha256(first).hexdigest()}.webp", hashlib.sha256(first).hexdigest()), "hash name")
        _check(len(list((folder / "species").iterdir())) == 1, "identical images are stored once")


def test_paths_cannot_come_from_data() -> None:
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name) / "static"
        for bad in ("../..", "../evil", "species/../..", "/etc", ""):
            _raises(ValueError, lambda bad=bad: img.save(b"x", bad, folder), f"folder must be refused: {bad!r}")
        _check(not folder.exists(), "nothing may be written")


def test_failed_save_leaves_nothing() -> None:
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        with mock.patch.object(img.os, "replace", side_effect=OSError("disk")):
            _raises(OSError, lambda: img.save(b"x", img.SUBDIR_SPECIES, folder), "the failure should surface")
        _check(list((folder / "species").iterdir()) == [], "no temp file left behind")


def _hash_name(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest() + ".webp"


def test_cleanup_only_touches_species_hash_files() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        static = root / "static"
        species, beaches = static / "species", static / "beaches"
        species.mkdir(parents=True)
        beaches.mkdir()
        keep, orphan = _hash_name("keep"), _hash_name("orphan")
        protected = [species / ".gitkeep", species / "notes.txt", species / "ABC.webp", beaches / _hash_name("beach"),
                     static / _hash_name("top"), root / _hash_name("outside")]
        for path in [species / keep, species / orphan, *protected]:
            path.write_bytes(b"x")
        (species / "subfolder").mkdir()
        target = root / "target.webp"
        target.write_bytes(b"x")
        link = species / _hash_name("link")
        link.symlink_to(target)
        referenced = {f"species/{keep}"}
        listed = img.remove_orphans(static, referenced, dry_run=True)
        _check(listed == [f"species/{orphan}"], f"dry run listed {listed}")
        _check((species / orphan).exists(), "a dry run must delete nothing")
        _check(img.remove_orphans(static, referenced, dry_run=False) == listed, "real run")
        _check(not (species / orphan).exists(), "the orphan should go")
        for path in [species / keep, *protected, target, link, species / "subfolder"]:
            _check(path.exists() or path.is_symlink(), f"must not be touched: {path.name}")
        _check(img.remove_orphans(root / "none", set(), dry_run=False) == [], "a missing folder is fine")


def test_refuses_an_older_pillow() -> None:
    _check(int(img.PIL.__version__.split(".")[0]) >= img.MIN_PILLOW_MAJOR, "this suite must run on Pillow 13")
    source = (Path(img.__file__)).read_text(encoding="utf-8")
    guard = source.index("raise ImportError")
    _check(guard < source.index("def small_url_for"), "the version check must run at import")


def main() -> int:
    logging.disable(logging.CRITICAL)
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    for name, test in tests:
        test()
        print(f"  ok {name}")
    print(f"All {len(tests)} image pipeline tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
