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

"""Pipeline configuration: EEZ bbox, seed zones, risk thresholds, and wind-drift factor (loaded from env)."""

from __future__ import annotations

import datetime as dt  # noqa: F401 – used in open_meteo_params type annotations
import os

from dotenv import load_dotenv

load_dotenv()

# --- Credentials / environment ------------------------------------------------
EE_PROJECT = os.environ.get("EE_PROJECT", "")
EE_SERVICE_ACCOUNT_JSON = os.environ.get("EE_SERVICE_ACCOUNT_JSON", "")
CMEMS_USERNAME = os.environ.get("CMEMS_USERNAME", "")
CMEMS_PASSWORD = os.environ.get("CMEMS_PASSWORD", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Shared secret echoed by Telegram in the X-Telegram-Bot-Api-Secret-Token header.
# Set this (and register the webhook with it) so the public /telegram/webhook
# endpoint rejects spoofed update payloads from anyone but Telegram.
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
API_BASE_URL = os.environ.get("API_BASE_URL", "")

# Open-Meteo atmospheric wind forecasting for future windage.
OPEN_METEO_BASE_URL = os.environ.get("OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast")
OPEN_METEO_HOURLY = os.environ.get("OPEN_METEO_HOURLY", "winddirection_10m,windspeed_10m")

# --- DR Exclusive Economic Zone (coarse bbox; refine later with real GeoJSON) -
# lat 17.3 .. 21.5, lon -72.1 .. -67.3
EEZ_BBOX = {
    "min_lon": -72.1,
    "min_lat": 17.3,
    "max_lon": -67.3,
    "max_lat": 21.5,
}


def eez_geojson() -> dict:
    """Return the EEZ bounding box as a GeoJSON Polygon (lon, lat order)."""
    b = EEZ_BBOX
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [b["min_lon"], b["min_lat"]],
                [b["max_lon"], b["min_lat"]],
                [b["max_lon"], b["max_lat"]],
                [b["min_lon"], b["max_lat"]],
                [b["min_lon"], b["min_lat"]],
            ]
        ],
    }


# --- Seed coastal zones (name -> center lat/lon) ------------------------------
ZONES = [
    {"name": "Punta Cana", "center_lat": 18.58, "center_lon": -68.37},
    {"name": "Bavaro", "center_lat": 18.68, "center_lon": -68.43},
    {"name": "Samana", "center_lat": 19.20, "center_lon": -69.33},
    {"name": "Puerto Plata", "center_lat": 19.80, "center_lon": -70.69},
    {"name": "Juan Dolio", "center_lat": 18.43, "center_lon": -69.42},
    # Southwest coast (Barahona / Pedernales) — covers Bahía de las Águilas,
    # Cabo Rojo and Playa Blanca, which were previously >200 km from any zone.
    {"name": "Barahona", "center_lat": 18.10, "center_lon": -71.10},
    {"name": "Pedernales", "center_lat": 17.90, "center_lon": -71.65},
    # Northwest coast (Monte Cristi) — covers Playa Juan de Bolaños, which is
    # ~100 km from Puerto Plata and otherwise out of the monitoring network.
    {"name": "Monte Cristi", "center_lat": 19.86, "center_lon": -71.65},
    # North-central coast (Río San Juan) — fills the visual gap between Puerto
    # Plata and Samáná zones; covers Playa Grande, Caletón, El Bretón, Diamante.
    {"name": "Rio San Juan", "center_lat": 19.62, "center_lon": -70.08},
    # South-central coast (Azua / Baní) — fills the visual gap between Juan
    # Dolio and Barahona zones; covers Playa Najayo, Palenque, Palmar de Ocoa.
    {"name": "Azua", "center_lat": 18.22, "center_lon": -70.45},
    # La Romana / Saona Island corridor — covers Bayahibe, Dominicus, Playa
    # Minitas, and both Saona Island beaches (Mano Juan, Canto de la Playa),
    # all of which were >54 km from any existing zone.
    {"name": "La Romana", "center_lat": 18.30, "center_lon": -68.76},
]

# Half-width (degrees) of the square box built around each zone center.
# 0.35° ≈ 39 km half-width (box ≈ 78 km × 74 km, corner ≈ 54 km). This is the
# "monitored area" around each coastal zone: a patch must drift INTO this box
# to count toward the zone's risk. Bigger boxes (e.g. the old 0.5°/111 km box)
# sum far too many scattered patches together and inflate every zone to 'high';
# smaller boxes risk missing genuine incoming masses. Tune via env var.
ZONE_BOX_HALF_DEG = float(os.environ.get("ZONE_BOX_HALF_DEG", "0.35"))


def zone_polygon_coords(center_lat: float, center_lon: float) -> list[list[float]]:
    """Build a small square polygon (GeoJSON ring, lon/lat) around a zone center."""
    d = ZONE_BOX_HALF_DEG
    return [
        [center_lon - d, center_lat - d],
        [center_lon + d, center_lat - d],
        [center_lon + d, center_lat + d],
        [center_lon - d, center_lat + d],
        [center_lon - d, center_lat - d],
    ]


# --- Detection thresholds (tune here) -----------------------------------------
# Sentinel-2 band roles and wavelengths (nm) used by the FAI baseline.
S2_BANDS = {"red": "B4", "nir": "B8", "swir": "B11"}
S2_WAVELENGTHS_NM = {"red": 665.0, "nir": 842.0, "swir": 1610.0}

# Reflectance scale factor for S2_SR_HARMONIZED (digital number -> reflectance).
S2_REFLECTANCE_SCALE = 10000.0

# Thresholds applied to floating-algae candidate pixels (reflectance units).
FAI_THRESHOLD = float(os.environ.get("FAI_THRESHOLD", "0.005"))
NDVI_MIN = float(os.environ.get("NDVI_MIN", "0.02"))

# Discard patches smaller than this (km^2) to suppress speckle noise.
MIN_PATCH_AREA_KM2 = float(os.environ.get("MIN_PATCH_AREA_KM2", "0.05"))

# Vectorization scale (m/pixel) for reduceToVectors. Coarser = faster/less noise.
DETECT_SCALE_M = float(os.environ.get("DETECT_SCALE_M", "60"))

# Maximum acceptable cloud cover (%) when filtering the S2 collection.
MAX_CLOUD_COVER_PCT = float(os.environ.get("MAX_CLOUD_COVER_PCT", "40"))

# Cloud Score+ (Google): keep pixels whose clear-sky score cs_cdf is at least
# this. Applied on top of the Sentinel-2 scene classification's cloud classes.
CLOUD_SCORE_MIN = float(os.environ.get("CLOUD_SCORE_MIN", "0.6"))
# FAI is compared with the local median FAI within this radius (m), which
# removes haze, sun glint and shallow-bottom brightness before thresholding.
# Larger windows were 5-20x slower on Earth Engine (see detect.py).
FAI_BG_RADIUS_M = float(os.environ.get("FAI_BG_RADIUS_M", "500"))
# Strip this much (m) off the coast before detecting: surf, river plumes and
# shallow reefs at the shoreline otherwise read as floating algae.
COAST_BUFFER_M = float(os.environ.get("COAST_BUFFER_M", "300"))
# FAI anomaly of a pixel fully covered by sargassum, used to estimate the
# covered area inside each outline (stored as covered_km2; risk still uses
# the outline area until reports allow re-tuning the thresholds). VERIFY.
S2_FAI_FULL_COVER = float(os.environ.get("S2_FAI_FULL_COVER", "0.08"))

DETECTION_SOURCE = "sentinel-2"

# --- NOAA/USF daily AFAI (independent check + cloudy-day gap fill) ------------
# USF's Alternative Floating Algae Index from MODIS/VIIRS, 0.015° (~1.6 km),
# one field per day, served free by NOAA/AOML's ERDDAP. Cloud and land cells
# are NaN; values are clipped to [-0.002, 0.004] and 0.004 means saturated.
AFAI_ENABLED = os.environ.get("AFAI_ENABLED", "1") not in ("0", "false", "False", "")
AFAI_GAP_FILL = os.environ.get("AFAI_GAP_FILL", "1") not in ("0", "false", "False", "")
AFAI_ERDDAP_URL = os.environ.get(
    "AFAI_ERDDAP_URL",
    "https://cwcgom.aoml.noaa.gov/erddap/griddap/noaa_aoml_atlantic_oceanwatch_AFAI_1D.json",
)
# How many of the most recent daily fields to combine (latest clear view per cell).
AFAI_DAYS = int(os.environ.get("AFAI_DAYS", "3"))
# A cell is flagged when its AFAI exceeds the local background (median over a
# AFAI_BG_WINDOW-cell square) by more than AFAI_ANOMALY_THRESHOLD. Chosen by
# inspecting real DR fields, not tuned against ground truth: tune it with the
# detection_checks table and beach reports.
AFAI_ANOMALY_THRESHOLD = float(os.environ.get("AFAI_ANOMALY_THRESHOLD", "0.001"))
AFAI_BG_WINDOW = int(os.environ.get("AFAI_BG_WINDOW", "21"))
# Cells this close (in grid cells) to a cloud or the coast are ignored: on
# real DR fields most bright cells sat right on a cloud or land edge.
AFAI_EDGE_BUFFER_CELLS = int(os.environ.get("AFAI_EDGE_BUFFER_CELLS", "2"))
AFAI_SATURATED = float(os.environ.get("AFAI_SATURATED", "0.0039"))
AFAI_MIN_CLUSTER_CELLS = int(os.environ.get("AFAI_MIN_CLUSTER_CELLS", "2"))
# AFAI of a cell fully covered by sargassum, used to turn the anomaly into a
# covered fraction (Wang & Hu 2016, MODIS). VERIFY before relying on areas.
AFAI_FULL_COVER = float(os.environ.get("AFAI_FULL_COVER", "0.0869"))
# Two detections closer than this (km) count as the same mass. Generous,
# because Sentinel-2 looks back 7 days and AFAI 3, and masses drift ~10-20 km/day.
AFAI_MATCH_KM = float(os.environ.get("AFAI_MATCH_KM", "15"))
AFAI_SOURCE = "usf-afai"

# --- Drift / risk -------------------------------------------------------------
# Surface currents: True uses Copernicus' merged product (ocean + tides +
# wave Stokes drift); False uses the ocean circulation alone.
CURRENTS_INCLUDE_STOKES = os.environ.get("CURRENTS_INCLUDE_STOKES", "1") not in ("0", "false", "False", "")

# Fraction of the 10 m wind added to the current ("windage"). Each ensemble
# member draws its own value between MIN and MAX, because real windage varies
# with mat thickness and sea state. A lab study measured just under 1 % for
# sargassum (Olascoaga, Beron-Vera et al.); with Stokes drift already in the
# currents, a higher value would count the wave push twice. WIND_DRIFT_FACTOR
# is the central value, used when the ensemble has a single member.
WIND_DRIFT_FACTOR = float(os.environ.get("WIND_DRIFT_FACTOR", "0.01"))
WIND_DRIFT_MIN = float(os.environ.get("WIND_DRIFT_MIN", "0.005"))
WIND_DRIFT_MAX = float(os.environ.get("WIND_DRIFT_MAX", "0.015" if CURRENTS_INCLUDE_STOKES else "0.02"))

# --- Particle ensemble (drift.py) ------------------------------------------
# Each patch is released as DRIFT_PARTICLES particles spread over its outline.
# Particle k of every patch belongs to ensemble member k, which shares one
# windage value, so each member is one plausible version of the next 3 days.
DRIFT_PARTICLES = int(os.environ.get("DRIFT_PARTICLES", "24"))
# Horizontal eddy diffusivity (m²/s) for the random walk: the sub-grid
# spreading a 1/12° (~9 km) current model can't resolve.
DRIFT_DIFFUSIVITY_M2S = float(os.environ.get("DRIFT_DIFFUSIVITY_M2S", "20"))
DRIFT_SEED = int(os.environ.get("DRIFT_SEED", "12345"))
# Masses seen longer ago than this are not caught up and are dropped.
DRIFT_MAX_AGE_DAYS = float(os.environ.get("DRIFT_MAX_AGE_DAYS", "8"))
# While catching a mass up from its observation time, arrivals in the last
# DRIFT_PRESENT_HOURS count as "present now"; earlier ones are not counted.
DRIFT_PRESENT_HOURS = float(os.environ.get("DRIFT_PRESENT_HOURS", "48"))
# A particle "arrives" when it comes this close to the coast (km), or hits land.
ARRIVAL_KM = float(os.environ.get("ARRIVAL_KM", "3"))
# Coastline belongs to its nearest zone centre if within this distance (km).
# Nearest-centre ownership means overlapping zones never share a beach.
ZONE_COAST_RADIUS_KM = float(os.environ.get("ZONE_COAST_RADIUS_KM", "40"))
# Headline risk = the highest level reached by at least this share of
# ensemble members (0.5 = the median member). Full probabilities are stored.
RISK_PROB_THRESHOLD = float(os.environ.get("RISK_PROB_THRESHOLD", "0.5"))

# Risk thresholds on projected patch area within a zone (km^2).
# These represent the TOTAL marine sargassum area drifting toward a zone:
#   low    : 0.5-5 km²  — visible traces likely on shore
#   medium : 5-25 km²   — noticeable accumulation expected
#   high   : >25 km²    — significant beach impact
# The minimum *significant* area to register ANY risk at all. Below this the
# mass is too small to produce meaningful beach impact even if it arrives.
RISK_AREA_MIN_KM2 = float(os.environ.get("RISK_AREA_MIN_KM2", "0.5"))   # ~700m × 700m
RISK_AREA_LOW_MAX_KM2 = float(os.environ.get("RISK_AREA_LOW_MAX_KM2", "5.0"))
RISK_AREA_MEDIUM_MAX_KM2 = float(os.environ.get("RISK_AREA_MEDIUM_MAX_KM2", "25.0"))
# ETA threshold that upgrades medium→high when a LARGE mass is imminent.
RISK_HIGH_ETA_HOURS = int(os.environ.get("RISK_HIGH_ETA_HOURS", "24"))

# --- Extended forecast / ML model ----------------------------------------
# Physics drift is reliable up to FORECAST_HOURS_PHYSICS.  Beyond that the
# ML model takes over up to FORECAST_HOURS_EXTENDED, then seasonal climatology.
FORECAST_HOURS_PHYSICS = int(os.environ.get("FORECAST_HOURS_PHYSICS", "72"))
FORECAST_HOURS_EXTENDED = int(os.environ.get("FORECAST_HOURS_EXTENDED", "168"))  # 7 days
# Retrain the ML model after this many pipeline runs (approx weekly with 6-h cron).
ML_RETRAIN_EVERY_N_RUNS = int(os.environ.get("ML_RETRAIN_EVERY_N_RUNS", "28"))
# Use the ML model only if it beat the seasonal fallback on held-out,
# later-in-time data at its last training. Set to 0 to always use it.
ML_REQUIRE_SKILL = os.environ.get("ML_REQUIRE_SKILL", "1") not in ("0", "false", "False", "")
# Walk-forward folds used to score the model at each training.
ML_EVAL_FOLDS = int(os.environ.get("ML_EVAL_FOLDS", "3"))
# Switch the training labels from physics risk to visitor reports once the
# reports give this many (zone, day, lead) rows.
ML_MIN_REPORT_ROWS = int(os.environ.get("ML_MIN_REPORT_ROWS", "150"))
# Calibrate the classifier's probabilities (sigmoid, time-ordered folds).
ML_CALIBRATE = os.environ.get("ML_CALIBRATE", "1") not in ("0", "false", "False", "")


def open_meteo_params(lat: float, lon: float, start: dt.date, end: dt.date) -> dict[str, str]:
    """Build Open-Meteo request parameters for 10m wind forecast data."""
    return {
        "latitude": str(lat),
        "longitude": str(lon),
        "hourly": OPEN_METEO_HOURLY,
        # Open-Meteo's default is km/h; the drift maths needs m/s.
        "wind_speed_unit": "ms",
        "timezone": "UTC",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
