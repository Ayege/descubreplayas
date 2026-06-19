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

DETECTION_SOURCE = "sentinel-2"

# --- Drift / risk -------------------------------------------------------------
# Fraction of wind speed added to surface current to approximate windage.
WIND_DRIFT_FACTOR = float(os.environ.get("WIND_DRIFT_FACTOR", "0.02"))

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


def open_meteo_params(lat: float, lon: float, start: dt.date, end: dt.date) -> dict[str, str]:
    """Build Open-Meteo request parameters for 10m wind forecast data."""
    return {
        "latitude": str(lat),
        "longitude": str(lon),
        "hourly": OPEN_METEO_HOURLY,
        "timezone": "UTC",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
