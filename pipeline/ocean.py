"""Ocean data step: fetch Copernicus Marine surface currents and wind at detected patch points."""

from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Iterable, Optional

import certifi
import copernicusmarine
import requests

from pipeline import config

logger = logging.getLogger(__name__)

# --- CMEMS product / variable identifiers (VERIFY against the CMEMS catalogue) -
# Surface currents: global ocean physics analysis & forecast, hourly, 1/12 deg.
CURRENT_DATASET_ID = "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m"
CURRENT_VARS = ["uo", "vo"]  # eastward / northward sea water velocity (m/s)

WIND_DATASET_ID = None
WIND_VARS: list[str] = []

CURRENT_UNITS = "m/s"
WIND_UNITS = "m/s"

# Small padding (deg) so requested points fall inside the subset box.
_BBOX_PAD_DEG = 0.5


def _bbox(points: list[tuple[float, float]]) -> dict:
    """Compute a padded lon/lat bounding box around a set of (lat, lon) points."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return {
        "minimum_latitude": min(lats) - _BBOX_PAD_DEG,
        "maximum_latitude": max(lats) + _BBOX_PAD_DEG,
        "minimum_longitude": min(lons) - _BBOX_PAD_DEG,
        "maximum_longitude": max(lons) + _BBOX_PAD_DEG,
    }


def _open_dataset(dataset_id: str, variables: list[str], box: dict, start: dt.datetime, end: dt.datetime):
    """Open a CMEMS dataset subset as xarray. Verify kwargs against your toolbox version."""
    return copernicusmarine.open_dataset(
        dataset_id=dataset_id,
        variables=variables,
        start_datetime=start,
        end_datetime=end,
        username=config.CMEMS_USERNAME or None,
        password=config.CMEMS_PASSWORD or None,
        **box,
    )


def _fetch_open_meteo_wind(lat: float, lon: float, start: dt.datetime, end: dt.datetime) -> dict[str, list[float]]:
    """Fetch hourly wind forecasts from Open-Meteo and convert to u/v components."""
    params = config.open_meteo_params(lat, lon, start.date(), end.date())
    response = requests.get(
        config.OPEN_METEO_BASE_URL,
        params=params,
        timeout=30,
        verify=certifi.where(),
    )
    response.raise_for_status()
    payload = response.json()

    if "hourly" not in payload:
        raise ValueError("Open-Meteo response missing hourly data")

    hourly = payload["hourly"]
    times = [dt.datetime.fromisoformat(ts).replace(tzinfo=dt.timezone.utc) for ts in hourly["time"]]
    windspeed = [float(v) for v in hourly["windspeed_10m"]]
    winddir = [float(v) for v in hourly["winddirection_10m"]]

    # Convert meteorological wind direction to u/v (eastward/northward) components.
    # Open-Meteo direction is degrees from north (meteorological convention).
    u10 = [-(speed * math.sin(math.radians(direction))) for speed, direction in zip(windspeed, winddir)]
    v10 = [-(speed * math.cos(math.radians(direction))) for speed, direction in zip(windspeed, winddir)]

    return {
        "times": times,
        "u10": u10,
        "v10": v10,
    }


def _align_wind_to_times(cur_times: list[dt.datetime], wind_data: dict[str, list[float]]) -> tuple[list[Optional[float]], list[Optional[float]]]:
    """Align hourly Open-Meteo wind data to current forecast timestamps."""
    wind_times = wind_data["times"]
    u10 = wind_data["u10"]
    v10 = wind_data["v10"]

    aligned_u: list[Optional[float]] = []
    aligned_v: list[Optional[float]] = []
    for target in cur_times:
        nearest_idx = min(range(len(wind_times)), key=lambda j: abs((wind_times[j] - target).total_seconds()))
        aligned_u.append(u10[nearest_idx])
        aligned_v.append(v10[nearest_idx])
    return aligned_u, aligned_v


def _sample_point(ds, var: str, lat: float, lon: float) -> list[float]:
    """Nearest-neighbour time series for one variable at one point."""
    series = ds[var].sel(latitude=lat, longitude=lon, method="nearest")
    # Surface dataset may carry a depth dimension; take the shallowest level.
    if "depth" in series.dims:
        series = series.isel(depth=0)
    return [float(v) for v in series.values.ravel()]


def get_ocean_forcing(
    points: Iterable[tuple[float, float]],
    forecast_hours: int = 72,
) -> dict[int, dict]:
    """Fetch surface current (and wind) time series for a list of (lat, lon) points.

    Args:
        points: iterable of (lat, lon) tuples.
        forecast_hours: how far ahead to fetch, starting now (UTC).

    Returns:
        Dict keyed by point index -> {
            "lat", "lon",
            "times": [ISO strings],
            "u_current", "v_current": [m/s],
            "u_wind", "v_wind": [m/s] (may be None if wind fetch failed),
            "units": {"current": "m/s", "wind": "m/s"},
        }
    """
    pts = [(float(lat), float(lon)) for lat, lon in points]
    if not pts:
        logger.warning("get_ocean_forcing called with no points.")
        return {}

    start = dt.datetime.now(dt.timezone.utc)
    end = start + dt.timedelta(hours=forecast_hours)
    box = _bbox(pts)

    logger.info("Fetching currents for %d point(s), %sh ahead.", len(pts), forecast_hours)
    try:
        cur_ds = _open_dataset(CURRENT_DATASET_ID, CURRENT_VARS, box, start, end)
    except Exception:
        logger.exception("Failed to fetch CMEMS currents (%s).", CURRENT_DATASET_ID)
        raise

    cur_times = [dt.datetime.fromisoformat(str(t)).replace(tzinfo=dt.timezone.utc) for t in cur_ds["time"].values]

    result: dict[int, dict] = {}
    for i, (lat, lon) in enumerate(pts):
        entry = {
            "lat": lat,
            "lon": lon,
            "times": [str(t) for t in cur_times],
            "u_current": _sample_point(cur_ds, "uo", lat, lon),
            "v_current": _sample_point(cur_ds, "vo", lat, lon),
            "u_wind": None,
            "v_wind": None,
            "units": {"current": CURRENT_UNITS, "wind": WIND_UNITS},
        }

        try:
            wind_data = _fetch_open_meteo_wind(lat, lon, start, end)
            aligned_u, aligned_v = _align_wind_to_times(cur_times, wind_data)
            entry["u_wind"] = aligned_u
            entry["v_wind"] = aligned_v
        except Exception:
            logger.warning(
                "Open-Meteo wind fetch failed for point %d; continuing with currents only.",
                i,
                exc_info=True,
            )

        result[i] = entry

    logger.info("Ocean forcing assembled for %d point(s).", len(result))
    return result
