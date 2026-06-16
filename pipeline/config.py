"""Pipeline configuration: EEZ bbox, seed zones, risk thresholds, and wind-drift factor (loaded from env)."""

from __future__ import annotations

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
]

# Half-width (degrees) of the square box built around each zone center.
# 0.5° ≈ 55 km — large enough to catch incoming sargassum with realistic forecast uncertainty.
ZONE_BOX_HALF_DEG = 0.5


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
RISK_AREA_LOW_MAX_KM2 = 1.0
RISK_AREA_MEDIUM_MAX_KM2 = 10.0
RISK_HIGH_ETA_HOURS = 24


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
