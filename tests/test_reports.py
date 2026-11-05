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

"""Offline tests for POST /reports. The database layer is replaced by fakes.

Run from repo root:
    python -m tests.test_reports
"""
from __future__ import annotations

import datetime as dt
import sys

from fastapi.testclient import TestClient

from api import db, main

STORED: list[dict] = []


def _fake_get_beach(name: str):
    if name == "Playa Bávaro":
        return {"name": name, "latitude": 18.68, "longitude": -68.45}
    return None


def _fake_list_zones():
    return [
        {"id": 1, "name": "Punta Cana", "center_lat": 18.58, "center_lon": -68.37},
        {"id": 2, "name": "Bavaro", "center_lat": 18.68, "center_lon": -68.43},
        {"id": 3, "name": "Puerto Plata", "center_lat": 19.80, "center_lon": -70.69},
    ]


def _fake_insert_report(**row):
    STORED.append(row)
    return {"id": len(STORED), **row}


def _client() -> TestClient:
    db.get_beach = _fake_get_beach
    db.list_zones = _fake_list_zones
    db.insert_report = _fake_insert_report
    main._rate_limit_hits.clear()
    return TestClient(main.app)


def test_valid_report_is_stored_with_nearest_zone() -> None:
    STORED.clear()
    resp = _client().post("/reports", json={"beach": "Playa Bávaro", "amount": "high"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["zone_id"] == 2 and body["amount"] == "high"
    assert body["observed_on"] == main._dr_today().isoformat()
    assert STORED[0]["beach_name"] == "Playa Bávaro"
    # Nothing personal reaches the database: only these fields are passed.
    assert set(STORED[0]) == {"beach_name", "latitude", "longitude", "zone_id",
                              "amount", "observed_on", "source"}, set(STORED[0])
    assert STORED[0]["source"] == "web"
    print("  valid report stored with nearest zone and DR date: OK")


def test_rejects_unknown_beach_bad_amount_and_dates() -> None:
    client = _client()
    assert client.post("/reports", json={"beach": "Nowhere", "amount": "low"}).status_code == 404
    assert client.post("/reports", json={"beach": "Playa Bávaro", "amount": "lots"}).status_code == 422
    old = (main._dr_today() - dt.timedelta(days=3)).isoformat()
    future = (main._dr_today() + dt.timedelta(days=1)).isoformat()
    for day in (old, future):
        r = client.post("/reports", json={"beach": "Playa Bávaro", "amount": "low", "observed_on": day})
        assert r.status_code == 422, (day, r.text)
    yesterday = (main._dr_today() - dt.timedelta(days=1)).isoformat()
    r = client.post("/reports", json={"beach": "Playa Bávaro", "amount": "none", "observed_on": yesterday})
    assert r.status_code == 201, r.text
    print("  unknown beach 404, bad amount/date 422, yesterday accepted: OK")


def test_rate_limited_per_visitor() -> None:
    client = _client()
    codes = [client.post("/reports", json={"beach": "Playa Bávaro", "amount": "low"},
                         headers={"X-Forwarded-For": "203.0.113.7"}).status_code
             for _ in range(main._RATE_LIMIT_MAX_REQUESTS + 1)]
    assert codes[-1] == 429 and codes[0] == 201
    other = client.post("/reports", json={"beach": "Playa Bávaro", "amount": "low"},
                        headers={"X-Forwarded-For": "203.0.113.8"})
    assert other.status_code == 201, "another visitor must not share the limit"
    print("  rate limit is per visitor: OK")


def test_match_beach() -> None:
    from api.reports import match_beach
    names = ["Playa Bávaro", "Playa Macao", "Playa Grande", "Playa Grande de Río San Juan"]
    assert match_beach("bavaro", names) == ("Playa Bávaro", [])
    assert match_beach("Playa BÁVARO", names) == ("Playa Bávaro", [])
    assert match_beach("macao", names) == ("Playa Macao", [])
    # "grande" is exactly one beach's name, so it wins over the longer one.
    assert match_beach("grande", names) == ("Playa Grande", [])
    assert match_beach("rio san", names) == ("Playa Grande de Río San Juan", [])
    assert match_beach("playa", names)[0] is None
    assert match_beach("nowhere", names) == (None, [])
    print("  accent/case-insensitive beach matching: OK")


def test_telegram_reporte() -> None:
    from api import telegram
    STORED.clear()
    sent: list[str] = []
    telegram.send_message = lambda chat_id, text: sent.append(text) or True
    db.list_beaches = lambda: [{"name": "Playa Bávaro"}, {"name": "Playa Macao"}]
    _client()  # installs the db fakes

    telegram._cmd_reporte(42, "/reporte mucho bávaro")
    assert STORED[-1]["amount"] == "high" and STORED[-1]["source"] == "telegram"
    assert STORED[-1]["beach_name"] == "Playa Bávaro" and STORED[-1]["zone_id"] == 2
    assert "42" not in str(STORED[-1].values()), "chat id is not stored"
    telegram._cmd_reporte(42, "/reporte Bávaro nada")       # amount last also works
    assert STORED[-1]["amount"] == "none"
    n = len(STORED)
    telegram._cmd_reporte(42, "/reporte bávaro")             # no amount
    telegram._cmd_reporte(42, "/reporte mucho")              # no beach
    telegram._cmd_reporte(42, "/reporte mucho Atlantis")     # unknown beach
    assert len(STORED) == n and "Uso" in sent[-3] and "No encontré" in sent[-1]
    print("  /reporte stores telegram reports, rejects bad input: OK")


def main_() -> int:
    for t in (test_valid_report_is_stored_with_nearest_zone,
              test_rejects_unknown_beach_bad_amount_and_dates,
              test_rate_limited_per_visitor,
              test_match_beach,
              test_telegram_reporte):
        print(f"\n{t.__name__}")
        t()
    print("\nAll reports assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())
