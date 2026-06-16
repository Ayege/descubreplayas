"""Pydantic schemas for API request and response payloads."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    db: str = "ok"
    ts: str = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())


class ZoneResponse(BaseModel):
    id: int
    name: str
    center_lat: float
    center_lon: float


class ForecastResponse(BaseModel):
    zone_id: int
    name: str
    risk_level: str
    eta_hours: Optional[int] = None
    eta_timestamp: Optional[str] = None
    run_at: str


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SubscribeRequest(BaseModel):
    channel: str = "telegram"
    chat_id: str
    zone_id: int
    role: str = "subscriber"


class SubscribeResponse(BaseModel):
    id: int
    channel: str
    chat_id: str
    zone_id: int
    role: str
    created_at: str
