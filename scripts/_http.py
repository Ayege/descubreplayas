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

"""The HTTP rules shared by every script that calls an outside service.

One client with explicit timeouts, no redirects, a cap on every response, and
retries on HTTP 429 and 5xx only. Scripts are the only code that touches the network.
"""
from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "DescubrePlayasRD/1.0 (+https://github.com/Ayege/descubreplayas)"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 60.0
INATURALIST_DELAY_S = 1.1  # iNaturalist asks for about one request per second.
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 3.0
MAX_RETRY_WAIT_S = 60.0
HTTP_TOO_MANY_REQUESTS = 429
RETRY_STATUSES = frozenset({HTTP_TOO_MANY_REQUESTS, 500, 502, 503, 504})
HTTP_OK = 200


class FetchError(Exception):
    """A request failed for good, or its response could not be trusted."""


class RateLimited(FetchError):
    """HTTP 429 from a caller that asked not to retry it."""


@dataclass(frozen=True)
class Api:
    """The one HTTP client for a run, and how to wait between requests."""

    client: httpx.Client
    sleep: Callable[[float], None] = time.sleep


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """One pooled client with explicit timeouts. TLS checks stay on by default."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        follow_redirects=False,
        transport=transport,
    )


def _read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise FetchError(f"response is over the {max_bytes} byte cap")
    return bytes(body)


def retry_wait(headers: Mapping[str, str], attempt: int) -> float:
    """Honour Retry-After; otherwise back off exponentially, with jitter."""
    try:
        wait = float(headers.get("Retry-After", ""))
    except ValueError:
        wait = BACKOFF_BASE_S * 2 ** attempt
    return min(wait + random.random(), MAX_RETRY_WAIT_S)  # noqa: S311 (jitter, not security)


def _parse_object(body: bytes) -> dict:
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise FetchError("response is not JSON") from exc
    if not isinstance(payload, dict):
        raise FetchError("response is not a JSON object")
    return payload


def _request(
    api: Api,
    url: str,
    params: Mapping[str, Any] | None,
    no_match: Callable[[int, bytes], bool] | None,
    delay: float,
    max_bytes: int,
    retry_statuses: frozenset[int] = RETRY_STATUSES,
) -> tuple[bytes, Mapping[str, str]] | None:
    """One GET, retrying only retry_statuses, at most MAX_ATTEMPTS times.

    Returns the body and its headers, or None when no_match recognises a failure
    response that really means "nothing found", which is not retried.
    """
    for attempt in range(MAX_ATTEMPTS):
        api.sleep(delay)
        with api.client.stream("GET", url, params=params) as response:
            body = _read_capped(response, max_bytes)
            status, headers = response.status_code, response.headers
        if status == HTTP_OK:
            return body, headers
        if no_match and no_match(status, body):
            return None
        if status not in retry_statuses:
            kind = RateLimited if status == HTTP_TOO_MANY_REQUESTS else FetchError
            raise kind(f"{url} returned HTTP {status}")
        logger.warning("http: %s returned HTTP %s, attempt %s", url, status, attempt + 1)
        api.sleep(retry_wait(headers, attempt))
    raise FetchError(f"{url} kept failing after {MAX_ATTEMPTS} attempts")


def get_json(
    api: Api,
    url: str,
    params: Mapping[str, Any] | None = None,
    no_match: Callable[[int, bytes], bool] | None = None,
    delay: float = INATURALIST_DELAY_S,
) -> dict:
    """GET one JSON object. no_match returns an empty object for "nothing found"."""
    result = _request(api, url, params, no_match, delay, MAX_RESPONSE_BYTES)
    return {} if result is None else _parse_object(result[0])


def get_json_with_headers(
    api: Api,
    url: str,
    params: Mapping[str, Any],
    delay: float,
    retry_statuses: frozenset[int] = RETRY_STATUSES,
) -> tuple[dict, Mapping[str, str]]:
    """GET one JSON object and its headers, for APIs that signal through both."""
    result = _request(api, url, params, None, delay, MAX_RESPONSE_BYTES, retry_statuses)
    assert result is not None  # no_match is None, so _request always returns a body
    return _parse_object(result[0]), result[1]


def get_bytes(
    api: Api,
    url: str,
    delay: float,
    max_bytes: int,
    retry_statuses: frozenset[int] = RETRY_STATUSES,
) -> tuple[bytes, str]:
    """GET a binary body and its Content-Type, aborting above max_bytes."""
    result = _request(api, url, None, None, delay, max_bytes, retry_statuses)
    assert result is not None  # no_match is None, so _request always returns a body
    return result[0], result[1].get("Content-Type", "")
