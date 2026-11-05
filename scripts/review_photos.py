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

"""Serve the contact sheet on this computer only, and save the photos you pick.

A static page can't write files, so this small server does it. It listens on 127.0.0.1
only, the address contains a secret token, and it writes exactly one file: the picks
file. Nothing here fetches or imports anything. Run it with the ingest environment:

    .venv-ingest/bin/python scripts/review_photos.py
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.beaches_data import BEACHES  # noqa: E402  (sys.path set above)
from dashboard.conoce_data import FORBIDDEN_TEXT_RE, MAX_ALT_CHARS  # noqa: E402
from scripts._commons import normalise_title  # noqa: E402
from scripts.fetch_species import serialise, write_atomic  # noqa: E402

logger = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"
INFO_DIR = Path(__file__).resolve().parent.parent / "info"
DEFAULT_SHEET = INFO_DIR / "photo_candidates.html"
DEFAULT_PICKS = INFO_DIR / "photo_picks.json"
MAX_BODY_BYTES = 256 * 1024
MAX_PICKS = 100
TOKEN_BYTES = 24
REQUEST_TIMEOUT_S = 10
PICK_KEYS = frozenset({"beach", "commons_file", "alt_es", "alt_en"})
_BEACH_NAMES = frozenset(beach["name"] for beach in BEACHES)
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class BadPicks(ValueError):
    """The submitted picks can't be saved. The message is safe to show: it never
    repeats what was sent.
    """


def _alt(entry: dict, key: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BadPicks("alt text must be text")
    text = value.strip()
    if not text:
        return None
    if FORBIDDEN_TEXT_RE.search(text) or len(text) > MAX_ALT_CHARS:
        raise BadPicks(f"alt text has markup or is over {MAX_ALT_CHARS} characters")
    return text


def parse_picks(payload: object) -> dict:
    """Check the picks and return them in a fixed shape. Alt text is optional here: it
    may be added later, and the importer requires it.
    """
    photos = payload.get("photos") if isinstance(payload, dict) and set(payload) == {"photos"} else None
    if not isinstance(photos, list) or len(photos) > MAX_PICKS:
        raise BadPicks(f"expected a list of at most {MAX_PICKS} photos")
    clean: list[dict] = []
    for entry in photos:
        if not isinstance(entry, dict) or not PICK_KEYS.issuperset(entry):
            raise BadPicks("a photo has unexpected fields")
        beach, title = entry.get("beach"), entry.get("commons_file")
        if not isinstance(beach, str) or beach not in _BEACH_NAMES:
            raise BadPicks("unknown beach")
        if not isinstance(title, str) or normalise_title(title) is None:
            raise BadPicks("a file title is not valid")
        item = {"beach": beach, "commons_file": normalise_title(title)}
        for key in ("alt_es", "alt_en"):
            text = _alt(entry, key)
            if text is not None:
                item[key] = text
        clean.append(item)
    names = [item["beach"] for item in clean]
    if len(set(names)) != len(names):
        raise BadPicks("more than one photo for a beach")
    return {"photos": clean}


class ReviewServer(HTTPServer):
    """Holds what the handler needs: the sheet to serve, the file to write, the token."""

    def __init__(self, sheet: Path, output: Path, token: str, port: int = 0) -> None:
        super().__init__((LOOPBACK, port), _Handler)
        self.sheet, self.output, self.token = sheet, output, token

    @property
    def allowed_hosts(self) -> frozenset[str]:
        port = self.server_address[1]
        return frozenset({f"{LOOPBACK}:{port}", f"localhost:{port}"})

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self.server_address[1]}/{self.token}/"


class _Handler(BaseHTTPRequestHandler):
    server: ReviewServer
    server_version = "PhotoReview"
    sys_version = ""
    timeout = REQUEST_TIMEOUT_S

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silent: the request line holds the token."""

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in {**_SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _from_this_page(self, needs_origin: bool) -> bool:
        """Refuse another site's page and DNS-rebinding: the Host must be ours, and a
        POST must say it came from our own origin.
        """
        host = self.headers.get("Host", "")
        if host not in self.server.allowed_hosts:
            return False
        origin = self.headers.get("Origin")
        if origin is None:
            return not needs_origin
        return origin == f"http://{host}"

    def _token_matches(self, path: str, suffix: str) -> bool:
        expected = f"/{self.server.token}/{suffix}"
        return hmac.compare_digest(path.encode("utf-8"), expected.encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        if not self._from_this_page(needs_origin=False):
            return self._json(403, {"error": "not allowed"})
        if not self._token_matches(self.path, ""):
            return self._json(404, {"error": "not found"})
        try:
            body = self.server.sheet.read_bytes()
        except OSError:
            return self._json(404, {"error": "the sheet is missing; run beach_photo_candidates.py --html first"})
        self._send(200, body, "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if not self._from_this_page(needs_origin=True):
            return self._json(403, {"error": "not allowed"})
        if not self._token_matches(self.path, "submit"):
            return self._json(404, {"error": "not found"})
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return self._json(415, {"error": "send JSON"})
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._json(411, {"error": "length required"})
        if not 0 <= length <= MAX_BODY_BYTES:
            return self._json(413, {"error": "too large"})
        try:
            picks = parse_picks(json.loads(self.rfile.read(length)))
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            message = str(exc) if isinstance(exc, BadPicks) else "not valid JSON"
            return self._json(400, {"error": message})
        try:
            write_atomic(self.server.output, serialise(picks))
        except OSError:
            logger.error("review_photos: could not write the picks file")
            return self._json(500, {"error": "could not save the picks file"})
        self._json(200, {"saved": len(picks["photos"]), "path": self.server.output.name})

    def _refuse_method(self) -> None:
        self._json(405, {"error": "not allowed"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse_method  # noqa: N815


def make_server(sheet: Path, output: Path, port: int = 0) -> ReviewServer:
    """A server with a fresh token, listening on this computer only."""
    return ReviewServer(sheet, output, secrets.token_urlsafe(TOKEN_BYTES), port)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET, help="contact sheet to serve")
    parser.add_argument("--output", type=Path, default=DEFAULT_PICKS, help="picks file to write")
    parser.add_argument("--no-browser", action="store_true", help="print the address and don't open it")
    args = parser.parse_args(argv)
    if not args.sheet.is_file():
        print(f"No sheet at {args.sheet}. Run beach_photo_candidates.py --html first.", file=sys.stderr)
        return 1
    server = make_server(args.sheet, args.output)
    print(f"Open {server.url}\nPicks are saved to {args.output}. Press Ctrl-C to stop.")
    if not args.no_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
