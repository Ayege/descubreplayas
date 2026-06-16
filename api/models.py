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
    horizons: Optional[list[dict]] = None


class DetectionResponse(BaseModel):
    id: int
    run_at: str
    lat: float
    lon: float
    area_km2: float
    source: Optional[str] = None


class BeachResponse(BaseModel):
    id: Optional[int] = None
    name: str
    province: str
    region: str
    latitude: float
    longitude: float
    access_type: Optional[str] = None
    access_description: Optional[str] = None
    entrance_fee: Optional[str] = None
    parking: bool = True
    beach_type: list[str] = Field(default_factory=list)
    activities: list[str] = Field(default_factory=list)
    wildlife: list[str] = Field(default_factory=list)
    ecosystem: Optional[str] = None
    protected_area: bool = False
    facilities: list[str] = Field(default_factory=list)
    water_conditions: Optional[str] = None
    best_time_to_visit: Optional[str] = None
    description: Optional[str] = None
    google_maps_url: Optional[str] = None


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
