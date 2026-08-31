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

"""Telegram bot webhook handler, command handlers, and send helper.

Bot commands:
  /start                  - greeting + usage
  /subscribe <zone>       - register this chat_id for the named zone
  /status                 - latest forecast for the subscriber's first zone
  /stop                   - remove all subscriptions for this chat_id
"""
from __future__ import annotations

import logging
import datetime as dt
from typing import Any

import certifi
import requests as http

from pipeline import config

logger = logging.getLogger(__name__)

_TG_BASE = "https://api.telegram.org/bot{token}/{method}"


# ---------------------------------------------------------------------------
# Low-level send helper
# ---------------------------------------------------------------------------

def send_message(chat_id: str | int, text: str) -> bool:
    """Send a plain-text Telegram message.  Returns True on success."""
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — skipping send.")
        return False
    url = _TG_BASE.format(token=token, method="sendMessage")
    try:
        resp = http.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=10,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        return True
    except Exception:
        logger.exception("send_message to chat_id=%s failed.", chat_id)
        return False


# Alias used by dispatch.py
send_alert = send_message


# ---------------------------------------------------------------------------
# Webhook registration helper (call once after deploy)
# ---------------------------------------------------------------------------

def register_webhook(webhook_url: str) -> bool:
    """Register webhook_url with the Telegram Bot API.

    When TELEGRAM_WEBHOOK_SECRET is configured it is sent as the webhook
    secret_token; Telegram then echoes it back in the
    X-Telegram-Bot-Api-Secret-Token header on every update so the receiver
    can authenticate the caller.
    """
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set.")
    url = _TG_BASE.format(token=token, method="setWebhook")
    payload: dict[str, Any] = {"url": webhook_url}
    if config.TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = config.TELEGRAM_WEBHOOK_SECRET
    resp = http.post(url, json=payload, timeout=10, verify=certifi.where())
    resp.raise_for_status()
    data = resp.json()
    logger.info("setWebhook response: %s", data)
    return bool(data.get("ok"))


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_start(chat_id: int) -> None:
    send_message(
        chat_id,
        "Hola! Soy el bot de alerta de sargazo para la República Dominicana.\n"
        "Comandos:\n"
        "  /subscribe <zona>  — activar alertas (ej: /subscribe Punta Cana)\n"
        "  /status            — ver el pronóstico actual\n"
        "  /stop              — desactivar alertas",
    )


def _cmd_subscribe(chat_id: int, text: str) -> None:
    """Parse /subscribe <zone name> and upsert a subscriber row."""
    # text looks like '/subscribe Punta Cana'
    parts = text.strip().split(None, 1)
    if len(parts) < 2:
        send_message(chat_id, "Uso: /subscribe <nombre de zona>  Ej: /subscribe Punta Cana")
        return

    zone_name = parts[1].strip()

    # Resolve zone_id from the DB.
    from api.db import get_client, insert_subscriber
    sb = get_client()
    result = sb.table("zones").select("id, name").ilike("name", f"%{zone_name}%").execute()
    zones = result.data or []
    if not zones:
        send_message(chat_id, f"No encontré la zona '{zone_name}'. Zonas disponibles: Punta Cana, Bavaro, Samana, Puerto Plata, Juan Dolio")
        return

    zone = zones[0]
    insert_subscriber(
        channel="telegram",
        chat_id=str(chat_id),
        zone_id=zone["id"],
        role="subscriber",
    )
    send_message(chat_id, f"✓ Suscrito a alertas para {zone['name']}.")


def _cmd_status(chat_id: int) -> None:
    """Reply with the latest forecast for the user's subscribed zone(s)."""
    from api.db import get_client
    sb = get_client()

    # Find zones this chat_id is subscribed to.
    subs = (
        sb.table("subscribers")
        .select("zone_id, zones(name)")
        .eq("chat_id", str(chat_id))
        .execute()
        .data or []
    )
    if not subs:
        send_message(chat_id, "No tienes zonas suscritas. Usa /subscribe <zona>.")
        return

    lines = []
    for sub in subs:
        zone_id = sub["zone_id"]
        zone_name = (sub.get("zones") or {}).get("name", str(zone_id))
        fc_result = (
            sb.table("forecasts")
            .select("risk_level, eta_hours, eta_timestamp")
            .eq("zone_id", zone_id)
            .order("run_at", desc=True)
            .limit(1)
            .execute()
        )
        fc = (fc_result.data or [None])[0]
        if fc:
            eta_str = f"~{fc['eta_hours']}h" if fc["eta_hours"] else "N/A"
            lines.append(f"{zone_name}: riesgo {fc['risk_level'].upper()} | llegada {eta_str}")
        else:
            lines.append(f"{zone_name}: sin pronóstico disponible")

    send_message(chat_id, "Pronóstico actual:\n" + "\n".join(lines))


def _cmd_stop(chat_id: int) -> None:
    from api.db import get_client
    sb = get_client()
    sb.table("subscribers").delete().eq("chat_id", str(chat_id)).execute()
    send_message(chat_id, "Alertas desactivadas. Usa /subscribe <zona> para reactivar.")


# ---------------------------------------------------------------------------
# Main dispatcher — called by api/main.py POST /telegram/webhook
# ---------------------------------------------------------------------------

async def handle_update(update: dict[str, Any]) -> None:
    """Route an incoming Telegram update to the appropriate command handler."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id: int = message["chat"]["id"]
    text: str = (message.get("text") or "").strip()

    logger.info("Telegram update from chat_id=%s: %r", chat_id, text[:80])

    if text.startswith("/start"):
        _cmd_start(chat_id)
    elif text.lower().startswith("/subscribe"):
        _cmd_subscribe(chat_id, text)
    elif text.startswith("/status"):
        _cmd_status(chat_id)
    elif text.startswith("/stop"):
        _cmd_stop(chat_id)
    else:
        send_message(chat_id, "Comando no reconocido. Usa /start para ver opciones.")
