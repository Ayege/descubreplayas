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

"""Pydantic schemas for API request and response payloads."""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

RiskLevel = Literal["none", "low", "medium", "high"]
ForecastMethod = Literal["ml", "seasonal"]


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
    # GeoJSON MultiLineString: the coastline this zone owns (where its
    # arrivals are counted). None until the pipeline has stored it.
    coast: Optional[dict] = None


class ForecastResponse(BaseModel):
    zone_id: int
    name: str
    risk_level: RiskLevel
    eta_hours: Optional[int] = None
    eta_timestamp: Optional[str] = None
    run_at: str
    horizons: Optional[list[dict]] = None
    # Share of drift-ensemble members reaching the zone's coast within 72 h,
    # and the share at each risk level.
    arrival_probability: Optional[float] = None
    risk_probs: Optional[dict[str, float]] = None
    model_version: Optional[str] = None


class BeachForecastResponse(BaseModel):
    """The pipeline's own 72-h forecast for one catalogue beach."""
    beach_name: str
    zone_id: Optional[int] = None
    risk_level: RiskLevel
    eta_hours: Optional[int] = None
    arrival_probability: Optional[float] = None
    risk_probs: Optional[dict[str, float]] = None
    horizons: Optional[list[dict]] = None
    run_at: str
    model_version: Optional[str] = None
    # +1: the next 24 h of currents and wind push onto this beach; -1: away.
    exposure: Optional[float] = None


class DetectionResponse(BaseModel):
    id: int
    run_at: str
    lat: float
    lon: float
    area_km2: float
    source: Optional[str] = None
    # Drift particles' median position now and at +24/+48/+72 h:
    # [{"h", "lat", "lon", "ashore"}]. Absent for patches too old to drift.
    track: Optional[list[dict]] = None


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

class ExtendedForecastResponse(BaseModel):
    zone_id: int
    zone_name: str
    lead_days: Literal[7, 14, 21]
    risk_level: RiskLevel
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    method: ForecastMethod
    valid_at: str
    run_at: str
    # Calibrated probability of each risk level (ML forecasts only).
    probs: Optional[dict[str, float]] = None
    model_version: Optional[str] = None


class SubscribeRequest(BaseModel):
    channel: Literal["telegram"] = "telegram"
    chat_id: str = Field(min_length=1, max_length=64, pattern=r"^-?\d+$")
    zone_id: int = Field(ge=1)
    role: Literal["subscriber", "fisherman", "hotel"] = "subscriber"


class ReportRequest(BaseModel):
    """A visitor's own observation of sargassum on a beach.

    `amount` uses the same four levels as the forecast so reports can be
    scored against it directly. `observed_on` defaults to today in the DR;
    the API accepts only today or yesterday, so old or future dates can't be
    used to rewrite history.
    """
    beach: str = Field(min_length=1, max_length=120)
    amount: RiskLevel
    observed_on: Optional[dt.date] = None


class ReportResponse(BaseModel):
    id: int
    beach: str
    amount: RiskLevel
    observed_on: str
    zone_id: Optional[int] = None


class SubscribeResponse(BaseModel):
    id: int
    channel: str
    chat_id: str
    zone_id: int
    role: str
    created_at: str
