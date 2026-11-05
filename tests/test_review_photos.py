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

"""Offline tests for scripts/review_photos.py: a real server on 127.0.0.1, fake requests.

Needs Pillow 13 (it imports the scripts package), so run it with the ingest environment:

    .venv-ingest/bin/python -m tests.test_review_photos
"""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import logging
import socket
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from dashboard.beaches_data import BEACHES
from scripts import review_photos as review

_BEACH = "Playa Bávaro"
_PICK = {"beach": _BEACH, "commons_file": "File:Beach.jpg", "alt_es": "Mar y palmeras", "alt_en": "Sea and palms"}
_SHEET = "<!doctype html><title>sheet</title>"


def _check(condition: bool, message: str) -> None:
    assert condition, message


@contextmanager
def _running(sheet_text: str | None = _SHEET) -> Iterator[tuple[review.ReviewServer, Path]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sheet = root / "sheet.html"
        if sheet_text is not None:
            sheet.write_text(sheet_text, encoding="utf-8")
        server = review.make_server(sheet, root / "picks.json")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, root / "picks.json"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


def _request(server: review.ReviewServer, method: str, path: str, body: bytes | None = None,
             headers: dict[str, str] | None = None, host: str | None = None) -> tuple[int, dict[str, str], bytes]:
    port = server.server_address[1]
    connection = http.client.HTTPConnection(review.LOOPBACK, port, timeout=5)
    try:
        send = {"Host": host if host is not None else f"127.0.0.1:{port}", **(headers or {})}
        connection.request(method, path, body=body, headers=send)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def _post(server: review.ReviewServer, payload: object, **overrides: object) -> tuple[int, dict]:
    port = server.server_address[1]
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", **overrides.pop("headers", {})}
    path = overrides.pop("path", f"/{server.token}/submit")
    status, _headers, data = _request(server, "POST", path, body, headers, **overrides)
    return status, json.loads(data) if data else {}


def test_listens_on_this_computer_only() -> None:
    with _running() as (server, _picks):
        _check(server.server_address[0] == "127.0.0.1", "bound to loopback, not to every interface")
        _check(server.server_address[1] > 0 and server.url.startswith("http://127.0.0.1:"), "a real port")
        _check(len(server.token) >= 30, "a long secret token")
    with _running() as (other, _picks):
        _check(other.token != server.token, "a fresh token each run")


def test_serves_the_sheet_only_at_the_secret_address() -> None:
    with _running() as (server, _picks):
        status, headers, body = _request(server, "GET", f"/{server.token}/")
        _check(status == 200 and body == _SHEET.encode("utf-8"), "the sheet")
        _check(headers["Content-Type"].startswith("text/html") and headers["Cache-Control"] == "no-store", "headers")
        _check(headers["X-Content-Type-Options"] == "nosniff" and headers["Referrer-Policy"] == "no-referrer", "security headers")
        _check(not any(name.lower().startswith("access-control") for name in headers), "no CORS: other sites can't read it")
        for path in ("/", "/index.html", f"/{server.token}", f"/{server.token}x/", "/wrong/", f"/{server.token}/../x", "/../etc/passwd"):
            status, _h, body = _request(server, "GET", path)
            _check(status == 404 and b"sheet" not in body, f"{path} must not serve anything: {status}")
    with _running(None) as (server, _picks):
        status, _h, body = _request(server, "GET", f"/{server.token}/")
        _check(status == 404 and b"candidates" in body, "a missing sheet says how to make it")


def test_rejects_other_hosts_and_origins() -> None:
    with _running() as (server, picks):
        for host in ("evil.example", "evil.example:80", f"127.0.0.1.evil.example:{server.server_address[1]}", "127.0.0.1", ""):
            status, _h, _b = _request(server, "GET", f"/{server.token}/", host=host)
            _check(status == 403, f"Host {host!r} must be refused (DNS rebinding): {status}")
            status, _data = _post(server, {"photos": [_PICK]}, host=host)
            _check(status == 403, f"a POST with Host {host!r}")
        for origin in ("http://evil.example", "https://127.0.0.1", "null", "http://127.0.0.1:1"):
            status, _data = _post(server, {"photos": [_PICK]}, headers={"Origin": origin})
            _check(status == 403, f"Origin {origin!r} must be refused")
        port = server.server_address[1]
        status, _h, _b = _request(server, "POST", f"/{server.token}/submit", b"{}", {"Content-Type": "application/json"})
        _check(status == 403, "a POST with no Origin is refused")
        status, _data = _post(server, {"photos": [_PICK]}, host=f"localhost:{port}", headers={"Origin": f"http://localhost:{port}"})
        _check(status == 200, "the page opened as localhost still works")
        _check(picks.exists(), "and it saved")


def test_saves_valid_picks_atomically_in_a_fixed_shape() -> None:
    with _running() as (server, picks):
        status, data = _post(server, {"photos": [_PICK, {"beach": "Playa Macao", "commons_file": "File:Other_one.jpg"}]})
        _check(status == 200 and data == {"saved": 2, "path": "picks.json"}, f"{status} {data}")
        saved = json.loads(picks.read_text(encoding="utf-8"))
        _check(saved["photos"][0] == _PICK, "kept as sent")
        _check(saved["photos"][1] == {"beach": "Playa Macao", "commons_file": "File:Other one.jpg"}, "alt is optional; titles are normalised")
        _check([p.name for p in picks.parent.iterdir() if p.suffix == ".tmp"] == [], "no temp file left behind")
        _check(picks.read_text(encoding="utf-8").endswith("\n"), "ends with a newline")
        status, data = _post(server, {"photos": [_PICK | {"alt_es": "  ", "alt_en": ""}]})
        _check(status == 200 and "alt_es" not in json.loads(picks.read_text(encoding="utf-8"))["photos"][0], "blank alt text is dropped")
        status, data = _post(server, {"photos": []})
        _check(status == 200 and json.loads(picks.read_text(encoding="utf-8")) == {"photos": []}, "clearing all picks is allowed")


def test_refuses_bad_picks_and_writes_nothing() -> None:
    bad = {
        "not an object": [],
        "extra top-level key": {"photos": [], "x": 1},
        "photos not a list": {"photos": "x"},
        "too many": {"photos": [_PICK] * (review.MAX_PICKS + 1)},
        "entry not an object": {"photos": ["x"]},
        "unknown field": {"photos": [_PICK | {"path": "/etc/passwd"}]},
        "unknown beach": {"photos": [_PICK | {"beach": "Playa Inventada"}]},
        "beach not text": {"photos": [_PICK | {"beach": ["Playa Bávaro"]}]},
        "bad title": {"photos": [_PICK | {"commons_file": "File:A.jpg|File:B.jpg"}]},
        "title not text": {"photos": [_PICK | {"commons_file": 5}]},
        "markup in alt": {"photos": [_PICK | {"alt_es": "<describe la escena>"}]},
        "alt too long": {"photos": [_PICK | {"alt_en": "a" * (review.MAX_ALT_CHARS + 1)}]},
        "alt not text": {"photos": [_PICK | {"alt_es": 5}]},
        "duplicate beach": {"photos": [_PICK, _PICK | {"commons_file": "File:Two.jpg"}]},
    }
    with _running() as (server, picks):
        for name, payload in bad.items():
            status, data = _post(server, payload)
            _check(status == 400 and data["error"], f"{name}: {status} {data}")
            _check("Inventada" not in data["error"] and "passwd" not in data["error"], f"{name}: the error repeats nothing sent")
            _check(not picks.exists(), f"{name}: nothing written")
        status, data = _post(server, b"{not json")
        _check(status == 400 and data == {"error": "not valid JSON"}, "bad JSON")
        status, data = _post(server, b"\xff\xfe")
        _check(status == 400, "bad bytes")
        status, data = _post(server, b"[" * 100_000 + b"]" * 100_000)
        _check(status == 400, "deeply nested JSON is refused, not a crash")
        status, data = _post(server, {"photos": [_PICK]})
        _check(status == 200, "and the server still answers afterwards")


def test_request_framing_rules() -> None:
    with _running() as (server, picks):
        status, _data = _post(server, {"photos": [_PICK]}, headers={"Content-Type": "text/plain"})
        _check(status == 415, "a plain-text POST (what a cross-site form can send) is refused")
        status, _data = _post(server, {"photos": [_PICK]}, headers={"Content-Type": "application/x-www-form-urlencoded"})
        _check(status == 415, "so is a form post")
        for claimed in (review.MAX_BODY_BYTES + 1, 10**12):
            status, _data = _post(server, b"{}", headers={"Content-Length": str(claimed)})
            _check(status == 413, f"a claimed length of {claimed} is refused before reading")
        status, _data = _post(server, b"{}", headers={"Content-Length": "-5"})
        _check(status == 413, "a negative length")
        status, _data = _post(server, b"{}", headers={"Content-Length": "abc"})
        _check(status == 411, "a non-numeric length")
        port = server.server_address[1]
        raw = (f"POST /{server.token}/submit HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\n"
               "Content-Type: application/json\r\nConnection: close\r\n\r\n").encode("ascii")
        with socket.create_connection((review.LOOPBACK, port), timeout=5) as sock:
            sock.sendall(raw)
            reply = sock.recv(4096)
        _check(reply.startswith(b"HTTP/1.0 411") or reply.startswith(b"HTTP/1.1 411"), f"no Content-Length: {reply[:40]!r}")
        status, _data = _post(server, {"photos": [_PICK]}, path=f"/{server.token}/other")
        _check(status == 404, "another path")
        status, _data = _post(server, {"photos": [_PICK]}, path="/wrong/submit")
        _check(status == 404, "a wrong token")
        status, _data = _post(server, {"photos": [_PICK]}, path=f"/{server.token}/submit?path=/etc/x")
        _check(status == 404 and not picks.exists(), "a query string changes nothing")
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
            status, _h, _b = _request(server, method, f"/{server.token}/submit", b"{}",
                                      {"Origin": f"http://127.0.0.1:{server.server_address[1]}"})
            _check(status == 405, f"{method} is not allowed")
        _check(not picks.exists(), "nothing was written by any of these")


def test_the_picks_cap_counts_distinct_beaches() -> None:
    names = [beach["name"] for beach in BEACHES[:4]]
    picks = [{"beach": name, "commons_file": "File:A.jpg"} for name in names]
    _check(review.MAX_PICKS == 100, "the agreed cap")
    with mock.patch.object(review, "MAX_PICKS", 3):
        try:
            review.parse_picks({"photos": picks})
        except review.BadPicks as exc:
            _check("at most 3" in str(exc), str(exc))
        else:
            raise AssertionError("four distinct beaches must be refused when the cap is three")
        _check(len(review.parse_picks({"photos": picks[:3]})["photos"]) == 3, "exactly at the cap is fine")


def test_a_failed_write_keeps_the_previous_file() -> None:
    with _running() as (server, picks):
        picks.write_text("PREVIOUS", encoding="utf-8")
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            status, data = _post(server, {"photos": [_PICK]})
        _check(status == 500 and data == {"error": "could not save the picks file"}, f"{status} {data}")
        _check(picks.read_text(encoding="utf-8") == "PREVIOUS", "the old picks survive a failed save")
        _check([p.name for p in picks.parent.iterdir() if p.suffix == ".tmp"] == [], "and no temp file is left")
        status, _data = _post(server, {"photos": [_PICK]})
        _check(status == 200, "the server keeps working")


def test_requests_are_not_logged() -> None:
    captured_err, captured_out = io.StringIO(), io.StringIO()
    with _running() as (server, _picks), contextlib.redirect_stderr(captured_err), contextlib.redirect_stdout(captured_out):
        _request(server, "GET", f"/{server.token}/")
        _post(server, {"photos": [_PICK]})
        _request(server, "GET", "/nope")
        token = server.token
    _check(token not in captured_err.getvalue() + captured_out.getvalue(), "the secret address never reaches a log")
    _check(captured_err.getvalue() == "" and captured_out.getvalue() == "", "and nothing is printed per request")


def test_it_writes_only_the_picks_file() -> None:
    with _running() as (server, picks):
        _post(server, {"photos": [_PICK]})
        names = sorted(path.name for path in picks.parent.iterdir())
        _check(names == ["picks.json", "sheet.html"], names)
        _check((picks.parent / "sheet.html").read_text(encoding="utf-8") == _SHEET, "the sheet is never modified")


def test_it_never_fetches_or_imports() -> None:
    source = Path(review.__file__).read_text(encoding="utf-8")
    for forbidden in ("import httpx", "import requests", "urllib.request", "subprocess", "os.system", "import_beach_photo", "make_client"):
        _check(forbidden not in source, f"review_photos.py must not use {forbidden}")


def test_main_refuses_a_missing_sheet() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _check(review.main(["--sheet", str(Path(tmp) / "none.html"), "--no-browser"]) == 1, "no sheet, no server")


def main() -> int:
    logging.disable(logging.CRITICAL)
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    for name, test in tests:
        test()
        print(f"  ok {name}")
    print(f"All {len(tests)} review server tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
