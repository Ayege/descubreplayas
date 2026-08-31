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

"""Dispatch step: match forecasts to subscribers, de-duplicate, and send Telegram alerts."""
from __future__ import annotations

import datetime as dt
import logging

from pipeline import config

logger = logging.getLogger(__name__)

# Minimum hours between repeat alerts for the same zone to prevent spam.
_ALERT_COOLDOWN_HOURS = 6


def _format_alert(zone_name: str, risk_level: str, eta_hours: int | None, eta_timestamp: str | None) -> str:
    """Format the alert message per the brief's Spanish template."""
    if eta_hours is not None and eta_timestamp:
        # Convert ISO timestamp to local DR time (UTC-4).
        try:
            eta_dt = dt.datetime.fromisoformat(eta_timestamp)
            dr_dt = eta_dt.astimezone(dt.timezone(dt.timedelta(hours=-4)))
            eta_local = dr_dt.strftime("%d/%m %H:%M")
        except Exception:
            eta_local = eta_timestamp[:16]
        eta_str = f"~{eta_hours}h ({eta_local} DR)"
    else:
        eta_str = "desconocida"

    return (
        f"ALERTA SARGAZO — {zone_name}: riesgo {risk_level.upper()}. "
        f"Llegada estimada {eta_str}. Planifica con tiempo."
    )


def dispatch_alerts(forecasts: list[dict]) -> int:
    """Send Telegram alerts to subscribers for medium/high risk zones.

    De-duplication: a subscriber is skipped if last_alerted is within
    _ALERT_COOLDOWN_HOURS (avoids repeat alerts on back-to-back pipeline runs).

    Args:
        forecasts: list of dicts from drift.project_drift() or store results,
                   each with {zone_id, zone_name, risk_level, eta_hours, eta_timestamp}.

    Returns:
        Number of alerts successfully sent.
    """
    if not forecasts:
        logger.info("dispatch: no forecasts to process.")
        return 0

    # Only alert on medium or high risk.
    alert_zones = {
        f["zone_id"]: f
        for f in forecasts
        if f.get("risk_level") in ("medium", "high")
    }
    if not alert_zones:
        logger.info("dispatch: no medium/high risk zones — nothing to send.")
        return 0

    try:
        from supabase import create_client
        sb = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    except Exception:
        logger.exception("dispatch: cannot connect to Supabase.")
        return 0

    try:
        from api.telegram import send_alert
    except Exception:
        logger.exception("dispatch: cannot import send_alert.")
        return 0

    now = dt.datetime.now(dt.timezone.utc)
    cooldown_cutoff = (now - dt.timedelta(hours=_ALERT_COOLDOWN_HOURS)).isoformat()

    sent = 0
    for zone_id, fc in alert_zones.items():
        # Fetch subscribers for this zone who haven't been alerted recently.
        try:
            result = (
                sb.table("subscribers")
                .select("id, chat_id, last_alerted")
                .eq("zone_id", zone_id)
                .eq("channel", "telegram")
                .execute()
            )
            subscribers = result.data or []
        except Exception:
            logger.exception("dispatch: subscriber query failed for zone_id=%s.", zone_id)
            continue

        message = _format_alert(
            fc["zone_name"],
            fc["risk_level"],
            fc.get("eta_hours"),
            fc.get("eta_timestamp"),
        )

        for sub in subscribers:
            last = sub.get("last_alerted")
            if last and last > cooldown_cutoff:
                logger.debug(
                    "dispatch: skipping chat_id=%s (alerted at %s, cooldown=%sh).",
                    sub["chat_id"], last, _ALERT_COOLDOWN_HOURS,
                )
                continue

            ok = send_alert(sub["chat_id"], message)
            if ok:
                sent += 1
                try:
                    sb.table("subscribers").update(
                        {"last_alerted": now.isoformat()}
                    ).eq("id", sub["id"]).execute()
                except Exception:
                    logger.warning("dispatch: failed to update last_alerted for sub id=%s.", sub["id"])
                logger.info(
                    "dispatch: alert sent to chat_id=%s for zone %s.",
                    sub["chat_id"], fc["zone_name"],
                )

    logger.info("dispatch: %d alert(s) sent.", sent)
    return sent
