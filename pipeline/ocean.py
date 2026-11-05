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

"""Ocean forcing step: gridded surface currents and 10 m wind over the EEZ.

The drift model needs the current WHERE THE MASS IS each hour, not where it
started, so this module downloads whole fields once per run and the drift
step interpolates them along every trajectory:

* Currents: Copernicus Marine global analysis & forecast, hourly, 1/12°.
  By default the merged product's `utotal`/`vtotal`, which add tides and
  wave-driven Stokes drift to the ocean circulation (`uo`/`vo`). Because the
  Stokes part is already in, the wind factor in config is lowered to match.
  Falls back to plain `uo`/`vo` if the merged product is unavailable.
* Wind: Open-Meteo 10 m wind on a 0.5° grid, in m/s.

Both cover from the oldest detection's observation time (the drift step
first catches each mass up to "now") to the end of the forecast.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass, field

import certifi
import numpy as np
import requests

from pipeline import config

logger = logging.getLogger(__name__)

# Merged surface currents (ocean + tide + Stokes). Variables confirmed with
# copernicusmarine.describe(): uo, vo, utide, vtide, vsdx, vsdy, utotal, vtotal.
MERGED_DATASET_ID = "cmems_mod_glo_phy_anfc_merged-uv_PT1H-i"
MERGED_VARS = ("utotal", "vtotal")
# Ocean circulation only (no tide, no Stokes drift).
OCEAN_DATASET_ID = "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m"
OCEAN_VARS = ("uo", "vo")

_PAD_DEG = 0.5            # margin around the EEZ so edge trajectories stay inside
_WIND_GRID_DEG = 0.5      # Open-Meteo sampling step
_HISTORICAL_WIND_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"


@dataclass
class ForcingField:
    """Hourly gridded currents and wind with vectorised interpolation.

    Times are seconds since the Unix epoch. Values outside the grid are 0
    (currents) or the nearest edge (time). Land cells in the current grid
    are 0 too: particles stop at the coast in the drift step anyway.
    """
    times: np.ndarray                     # (nt,)
    lats: np.ndarray                      # (ny,) ascending
    lons: np.ndarray                      # (nx,) ascending
    u: np.ndarray                         # (nt, ny, nx) m/s eastward
    v: np.ndarray                         # (nt, ny, nx) m/s northward
    current_kind: str = "total"           # "total" (incl. tide + Stokes) or "ocean"
    wind_times: np.ndarray | None = None
    wind_lats: np.ndarray | None = None
    wind_lons: np.ndarray | None = None
    wind_u: np.ndarray | None = None
    wind_v: np.ndarray | None = None
    _interp: dict = field(default_factory=dict, repr=False)

    @property
    def has_wind(self) -> bool:
        return self.wind_u is not None

    def _get(self, name: str, times, lats, lons, values):
        if name not in self._interp:
            from scipy.interpolate import RegularGridInterpolator
            self._interp[name] = RegularGridInterpolator(
                (times, lats, lons), values, bounds_error=False, fill_value=0.0
            )
        return self._interp[name]

    @staticmethod
    def _points(times: np.ndarray, t: float, lat, lon) -> np.ndarray:
        lat = np.atleast_1d(np.asarray(lat, dtype=float))
        lon = np.atleast_1d(np.asarray(lon, dtype=float))
        tt = np.full(lat.shape, float(np.clip(t, times[0], times[-1])))
        return np.column_stack([tt, lat, lon])

    def current(self, t: float, lat, lon) -> tuple[np.ndarray, np.ndarray]:
        pts = self._points(self.times, t, lat, lon)
        return (self._get("u", self.times, self.lats, self.lons, self.u)(pts),
                self._get("v", self.times, self.lats, self.lons, self.v)(pts))

    def wind(self, t: float, lat, lon) -> tuple[np.ndarray, np.ndarray]:
        if not self.has_wind:
            n = np.atleast_1d(lat).shape
            return np.zeros(n), np.zeros(n)
        pts = self._points(self.wind_times, t, lat, lon)
        # Clamp to the wind grid instead of zeroing outside it: wind varies
        # slowly and a particle just past the edge still feels it.
        pts[:, 1] = np.clip(pts[:, 1], self.wind_lats[0], self.wind_lats[-1])
        pts[:, 2] = np.clip(pts[:, 2], self.wind_lons[0], self.wind_lons[-1])
        return (self._get("wu", self.wind_times, self.wind_lats, self.wind_lons, self.wind_u)(pts),
                self._get("wv", self.wind_times, self.wind_lats, self.wind_lons, self.wind_v)(pts))

    @classmethod
    def uniform(cls, start: dt.datetime, hours: int, u: float, v: float,
                wind_u: float = 0.0, wind_v: float = 0.0, bbox: dict | None = None) -> "ForcingField":
        """Constant field over the EEZ: for tests and offline runs."""
        b = bbox or config.EEZ_BBOX
        t0 = start.timestamp()
        times = np.array([t0 + 3600 * h for h in range(-240, hours + 2)], dtype=float)
        lats = np.array([b["min_lat"] - 1, b["max_lat"] + 1])
        lons = np.array([b["min_lon"] - 1, b["max_lon"] + 1])
        shape = (len(times), 2, 2)
        return cls(times, lats, lons, np.full(shape, u), np.full(shape, v), "ocean",
                   times, lats, lons, np.full(shape, wind_u), np.full(shape, wind_v))


# ---------------------------------------------------------------------------
# Currents (Copernicus Marine)
# ---------------------------------------------------------------------------

def _padded_bbox(bbox: dict) -> dict:
    return {
        "minimum_latitude": bbox["min_lat"] - _PAD_DEG,
        "maximum_latitude": bbox["max_lat"] + _PAD_DEG,
        "minimum_longitude": bbox["min_lon"] - _PAD_DEG,
        "maximum_longitude": bbox["max_lon"] + _PAD_DEG,
    }


def _surface(da) -> np.ndarray:
    """(time, lat, lon) float array from a CMEMS variable, top level only."""
    if "depth" in da.dims:
        da = da.isel(depth=0)
    da = da.transpose("time", "latitude", "longitude")
    return np.nan_to_num(np.asarray(da.values, dtype=float), nan=0.0)


def _fetch_currents(start: dt.datetime, end: dt.datetime, bbox: dict):
    import copernicusmarine

    attempts = [(MERGED_DATASET_ID, MERGED_VARS, "total"), (OCEAN_DATASET_ID, OCEAN_VARS, "ocean")]
    if not config.CURRENTS_INCLUDE_STOKES:
        attempts = attempts[1:]
    last_exc: Exception | None = None
    for dataset_id, (uvar, vvar), kind in attempts:
        try:
            ds = copernicusmarine.open_dataset(
                dataset_id=dataset_id,
                variables=[uvar, vvar],
                start_datetime=start.replace(tzinfo=None),
                end_datetime=end.replace(tzinfo=None),
                minimum_depth=0.0,
                maximum_depth=1.0,
                username=config.CMEMS_USERNAME or None,
                password=config.CMEMS_PASSWORD or None,
                **_padded_bbox(bbox),
            )
            times = np.array([t.astype("datetime64[s]").astype(float) for t in ds["time"].values])
            u, v = _surface(ds[uvar]), _surface(ds[vvar])
            logger.info("ocean: %s %s/%s, %d h × %d × %d grid", dataset_id, uvar, vvar,
                        len(times), u.shape[1], u.shape[2])
            return (times, np.asarray(ds["latitude"].values, float),
                    np.asarray(ds["longitude"].values, float), u, v, kind)
        except Exception as exc:  # noqa: BLE001 — try the next product
            logger.warning("ocean: %s unavailable (%s); trying the next product.", dataset_id, exc)
            last_exc = exc
    raise RuntimeError("No Copernicus current product could be read.") from last_exc


# ---------------------------------------------------------------------------
# Wind (Open-Meteo)
# ---------------------------------------------------------------------------

def _wind_uv(speed: float, direction_deg: float) -> tuple[float, float]:
    """Meteorological direction (where the wind comes FROM) -> u, v (towards)."""
    return (-speed * math.sin(math.radians(direction_deg)),
            -speed * math.cos(math.radians(direction_deg)))


def _fetch_wind(start: dt.datetime, end: dt.datetime, bbox: dict):
    lats = np.arange(bbox["min_lat"] - _PAD_DEG, bbox["max_lat"] + _PAD_DEG + 1e-6, _WIND_GRID_DEG)
    lons = np.arange(bbox["min_lon"] - _PAD_DEG, bbox["max_lon"] + _PAD_DEG + 1e-6, _WIND_GRID_DEG)
    grid = [(round(float(la), 3), round(float(lo), 3)) for la in lats for lo in lons]
    params = config.open_meteo_params(grid[0][0], grid[0][1], start.date(), end.date())
    params["latitude"] = ",".join(str(la) for la, _ in grid)
    params["longitude"] = ",".join(str(lo) for _, lo in grid)
    # The forecast API only goes ~3 months back; older periods (replays) use
    # Open-Meteo's Historical Forecast API, which takes the same parameters.
    url = config.OPEN_METEO_BASE_URL
    if start < dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=80):
        url = _HISTORICAL_WIND_URL
    resp = requests.post(url, data=params, timeout=90, verify=certifi.where())
    if resp.status_code == 405:  # endpoint that only takes GET
        resp = requests.get(url, params=params, timeout=90, verify=certifi.where())
    resp.raise_for_status()
    payload = resp.json()
    entries = payload if isinstance(payload, list) else [payload]
    if len(entries) != len(grid):
        raise ValueError(f"Open-Meteo returned {len(entries)} points for {len(grid)} requested")

    hourly0 = entries[0]["hourly"]
    unit = (entries[0].get("hourly_units") or {}).get("windspeed_10m", "m/s")
    if unit not in ("m/s", "ms"):
        raise ValueError(f"Open-Meteo wind speed unit is {unit!r}, expected m/s")
    times = np.array([dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc).timestamp()
                      for t in hourly0["time"]], dtype=float)
    wu = np.zeros((len(times), len(lats), len(lons)))
    wv = np.zeros_like(wu)
    for k, entry in enumerate(entries):
        i, j = divmod(k, len(lons))
        h = entry["hourly"]
        for n, (s, d) in enumerate(zip(h["windspeed_10m"], h["winddirection_10m"])):
            if s is None or d is None:
                continue
            wu[n, i, j], wv[n, i, j] = _wind_uv(float(s), float(d))
    logger.info("ocean: Open-Meteo wind, %d h × %d × %d grid", len(times), len(lats), len(lons))
    return times, lats, lons, wu, wv


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_forcing(start: dt.datetime, end: dt.datetime, bbox: dict | None = None) -> ForcingField:
    """Currents (required) and wind (best effort) over the EEZ for [start, end]."""
    bbox = bbox or config.EEZ_BBOX
    times, lats, lons, u, v, kind = _fetch_currents(start, end, bbox)
    f = ForcingField(times, lats, lons, u, v, kind)
    try:
        f.wind_times, f.wind_lats, f.wind_lons, f.wind_u, f.wind_v = _fetch_wind(start, end, bbox)
    except Exception:
        logger.warning("ocean: wind fetch failed; drifting with currents only.", exc_info=True)
    return f
