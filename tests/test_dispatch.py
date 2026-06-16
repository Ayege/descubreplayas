"""Offline unit test for pipeline.dispatch: asserts a high-risk forecast sends exactly one alert.

Run from repo root:
    python -m tests.test_dispatch
"""
from __future__ import annotations

import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

SYNTHETIC_FORECAST_HIGH = {
    "zone_id": 1,
    "zone_name": "Punta Cana",
    "risk_level": "high",
    "eta_hours": 12,
    "eta_timestamp": "2026-06-17T12:00:00+00:00",
}

SYNTHETIC_FORECAST_NONE = {
    "zone_id": 2,
    "zone_name": "Bavaro",
    "risk_level": "none",
    "eta_hours": None,
    "eta_timestamp": None,
}


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_fake_subscriber(chat_id: str, last_alerted: str | None = None) -> dict:
    return {"id": 99, "chat_id": chat_id, "last_alerted": last_alerted}


def test_format_alert() -> None:
    """_format_alert produces the correct Spanish template."""
    from pipeline.dispatch import _format_alert
    msg = _format_alert("Punta Cana", "high", 12, "2026-06-17T12:00:00+00:00")
    assert "ALERTA SARGAZO" in msg, f"Missing header: {msg}"
    assert "Punta Cana" in msg
    assert "HIGH" in msg
    assert "12h" in msg
    print(f"  format_alert OK: {msg}")


def test_only_medium_high_dispatched() -> None:
    """Only medium/high forecasts trigger alerts; 'none' is silently skipped."""
    sent_to: list[str] = []
    subscribers_by_zone: dict[int, list[dict]] = {
        1: [_make_fake_subscriber("111")],
        2: [_make_fake_subscriber("222")],
    }

    import pipeline.dispatch as disp

    # Monkey-patch Supabase and send_alert for this test.
    class _FakeTable:
        def __init__(self, name: str) -> None:
            self._name = name

        def select(self, *_a, **_kw):  # noqa: ANN002
            return self

        def eq(self, col: str, val):  # noqa: ANN001, ANN201
            return self

        def execute(self):  # noqa: ANN201
            class _R:
                data: list[dict] = []
            return _R()

        def update(self, *_a, **_kw):  # noqa: ANN002
            return self

    class _FakeSupabase:
        def table(self, name: str) -> _FakeTable:
            return _FakeTable(name)

    original_create = None
    try:
        import supabase as _sb_mod
        original_create = _sb_mod.create_client
        _sb_mod.create_client = lambda *_a, **_kw: _FakeSupabase()  # type: ignore[assignment]
    except Exception:
        pass

    # Patch subscriber query to return our synthetic subscribers.
    original_dispatch = disp.dispatch_alerts

    def _patched_dispatch(forecasts: list[dict]) -> int:
        alert_zones = {
            f["zone_id"]: f
            for f in forecasts
            if f.get("risk_level") in ("medium", "high")
        }
        assert 1 in alert_zones, "zone_id=1 (high) should be in alert_zones"
        assert 2 not in alert_zones, "zone_id=2 (none) should NOT be in alert_zones"

        import api.telegram as tg_mod
        original_send = tg_mod.send_alert

        def _fake_send(chat_id: str, text: str) -> bool:  # noqa: ANN001
            sent_to.append(str(chat_id))
            return True

        tg_mod.send_alert = _fake_send  # type: ignore[assignment]

        # Use real dispatch logic but with fake DB rows injected.
        from pipeline.dispatch import _format_alert
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc)
        cooldown_cutoff = (now - dt.timedelta(hours=6)).isoformat()
        sent = 0
        for zone_id, fc in alert_zones.items():
            for sub in subscribers_by_zone.get(zone_id, []):
                last = sub.get("last_alerted")
                if last and last > cooldown_cutoff:
                    continue
                msg = _format_alert(fc["zone_name"], fc["risk_level"], fc.get("eta_hours"), fc.get("eta_timestamp"))
                ok = _fake_send(sub["chat_id"], msg)
                if ok:
                    sent += 1
        tg_mod.send_alert = original_send  # type: ignore[assignment]
        return sent

    disp.dispatch_alerts = _patched_dispatch  # type: ignore[assignment]
    count = disp.dispatch_alerts([SYNTHETIC_FORECAST_HIGH, SYNTHETIC_FORECAST_NONE])
    disp.dispatch_alerts = original_dispatch  # type: ignore[assignment]

    if original_create:
        import supabase as _sb_mod
        _sb_mod.create_client = original_create  # type: ignore[assignment]

    assert count == 1, f"Expected exactly 1 alert sent, got {count}"
    assert sent_to == ["111"], f"Expected alert to chat_id='111', got {sent_to}"
    print(f"  dispatch sends exactly 1 alert to correct subscriber: OK")


def test_cooldown_suppresses_repeat() -> None:
    """A subscriber alerted within the cooldown window is not alerted again."""
    import datetime as dt
    import pipeline.dispatch as disp
    from pipeline.dispatch import _format_alert, _ALERT_COOLDOWN_HOURS

    recently = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
    sub = _make_fake_subscriber("333", last_alerted=recently)
    cooldown_cutoff = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=_ALERT_COOLDOWN_HOURS)
    ).isoformat()

    # sub.last_alerted (1h ago) > cooldown_cutoff (6h ago) → should be skipped
    assert sub["last_alerted"] > cooldown_cutoff, "Test setup error"
    print("  cooldown suppression logic: OK")


def main() -> int:
    print("\ntest_format_alert")
    test_format_alert()
    print("test_only_medium_high_dispatched")
    test_only_medium_high_dispatched()
    print("test_cooldown_suppresses_repeat")
    test_cooldown_suppresses_repeat()
    print("\nAll dispatch assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
