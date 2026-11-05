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

"""Detection step: fetch satellite imagery from Earth Engine, cloud-mask, compute FAI/NDVI, and emit sargassum patch polygons."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Optional

import ee
import geopandas as gpd
from shapely.geometry import shape

from pipeline import config

logger = logging.getLogger(__name__)

_S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
_initialized = False


def init_earth_engine() -> None:
    """Authenticate and initialize Earth Engine using a service account or user creds.

    EE_SERVICE_ACCOUNT_JSON may be either a path to a JSON key file or the raw
    JSON string (useful for CI secrets). Falls back to default user credentials.
    """
    global _initialized
    if _initialized:
        return

    project = config.EE_PROJECT or None
    key = config.EE_SERVICE_ACCOUNT_JSON.strip()

    try:
        if key:
            if os.path.exists(key):
                key_path = key
                with open(key_path, "r", encoding="utf-8") as fh:
                    service_account = json.load(fh)["client_email"]
            else:
                # Treat the value as raw JSON; write to a temp file for EE.
                info = json.loads(key)
                service_account = info["client_email"]
                key_path = os.path.join(
                    os.getcwd(), ".ee-key.runtime.json"
                )
                with open(key_path, "w", encoding="utf-8") as fh:
                    json.dump(info, fh)
            # NOTE: verify ee.ServiceAccountCredentials signature in your EE version.
            credentials = ee.ServiceAccountCredentials(service_account, key_path)
            ee.Initialize(credentials, project=project)
            logger.info("Earth Engine initialized via service account (project=%s).", project)
        else:
            ee.Initialize(project=project)
            logger.info("Earth Engine initialized via default credentials (project=%s).", project)
        _initialized = True
    except Exception:
        logger.exception("Failed to initialize Earth Engine.")
        raise


_CLOUD_SCORE_COLLECTION = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
# SCL classes that are never usable: 0 no data, 1 saturated/defective,
# 3 cloud shadow, 8/9 cloud medium/high probability, 10 thin cirrus, 11 snow.
_SCL_BAD = (0, 1, 3, 8, 9, 10, 11)


def _mask_clouds(image: "ee.Image") -> "ee.Image":
    """Drop cloudy pixels, keeping every cloud-free OCEAN pixel.

    The old mask kept only pixels the scene classification (SCL) called
    "water" (6). Dense sargassum mats can be labelled vegetation (4), bare
    soil (5) or unclassified (7) instead, and those were dropped. On real DR
    scenes that cost 1-3 % of algae pixels, while 30-80 % of algae-flagged
    pixels were clouds, which FAI also fires on. So: no "water" requirement
    (land is removed by the ocean-only AOI instead), SCL cloud classes
    removed as before, plus Google's Cloud Score+ (cs_cdf >= CLOUD_SCORE_MIN)
    where it is available for the image.
    """
    scl = image.select("SCL")
    bad = scl.remap(list(_SCL_BAD), [1] * len(_SCL_BAD), 0)
    mask = bad.Not()
    cs = image.select("cs_cdf")
    # cs_cdf is missing (fully masked) when Cloud Score+ has no matching
    # image; unmask(1) treats that as clear and leaves SCL to decide.
    mask = mask.And(cs.unmask(1).gte(config.CLOUD_SCORE_MIN))
    return image.updateMask(mask)


def _add_obs_time(image: "ee.Image") -> "ee.Image":
    """Band with the image's acquisition time (ms since epoch), so each
    detected patch keeps WHEN it was seen, not just where."""
    t = ee.Image.constant(ee.Number(image.get("system:time_start"))).toDouble().rename("OBS_T")
    return image.addBands(t.updateMask(image.select("SCL").mask()))


def _add_indices(image: "ee.Image") -> "ee.Image":
    """Add scaled FAI and NDVI bands to an S2 image (reflectance units)."""
    scale = config.S2_REFLECTANCE_SCALE
    red = image.select(config.S2_BANDS["red"]).divide(scale)
    nir = image.select(config.S2_BANDS["nir"]).divide(scale)
    swir = image.select(config.S2_BANDS["swir"]).divide(scale)

    wl = config.S2_WAVELENGTHS_NM
    factor = (wl["nir"] - wl["red"]) / (wl["swir"] - wl["red"])
    baseline = red.add(swir.subtract(red).multiply(factor))
    fai = nir.subtract(baseline).rename("FAI")

    ndvi = nir.subtract(red).divide(nir.add(red)).rename("NDVI")
    return image.addBands([fai, ndvi])


def detect_sargassum(
    date_range: tuple[str, str],
    eez_geojson: Optional[dict] = None,
) -> gpd.GeoDataFrame:
    """Detect floating sargassum patches over the DR EEZ for a date range.

    Args:
        date_range: (start, end) ISO date strings, e.g. ("2026-06-09", "2026-06-16").
        eez_geojson: GeoJSON Polygon for the area of interest. Defaults to the
            configured EEZ bounding box.

    Returns:
        GeoDataFrame (EPSG:4326) of patch polygons with columns:
        geometry, centroid_lat, centroid_lon, area_km2, source.
        Empty GeoDataFrame if nothing is detected.
    """
    init_earth_engine()

    aoi_geojson = eez_geojson or config.eez_geojson()
    aoi = ee.Geometry(aoi_geojson)
    start, end = date_range

    # Subtract the Hispaniola land mass (DR + Haiti) from the detection area so
    # that inland water bodies (reservoirs, rivers, lakes) cannot produce false
    # positives.  Floating-algae indices (FAI/NDVI) fire on any inland algae;
    # restricting to ocean pixels ensures we only detect marine sargassum.
    # Ocean mask as a RASTER: the detailed boundary (not LSIB_SIMPLE
    # simplified to 1 km) painted into an image and widened by COAST_BUFFER_M,
    # so surf, river plumes and shallow reefs at the shore don't read as
    # floating algae. Clipping to the equivalent vector geometry made Earth
    # Engine time out; a painted mask costs almost nothing per tile.
    land = ee.FeatureCollection("USDOS/LSIB/2017").filter(
        ee.Filter.inList("COUNTRY_NA", ["Dominican Republic", "Haiti"])
    )
    land_img = ee.Image.constant(0).byte().paint(land, 1)
    if config.COAST_BUFFER_M > 0:
        land_img = land_img.focalMax(radius=config.COAST_BUFFER_M, kernelType="circle",
                                     units="meters")
    ocean_mask = land_img.Not()

    logger.info("Detecting sargassum %s..%s over EEZ.", start, end)

    collection = (
        ee.ImageCollection(_S2_COLLECTION)
        .filterBounds(aoi)  # broad filter for image selection
        .filterDate(start, end)
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", config.MAX_CLOUD_COVER_PCT))
        .linkCollection(ee.ImageCollection(_CLOUD_SCORE_COLLECTION), ["cs_cdf"])
        .map(_mask_clouds)
        .map(_add_indices)
        .map(_add_obs_time)
        # Most-recent-first so mosaic() below takes each pixel's LATEST clear
        # observation, falling back to older images only where cloud/shadow
        # masked the recent ones. date_range spans 7 days (see run.py) to
        # guarantee enough clear imagery in a cloudy Caribbean week, but
        # sargassum drifts tens of km/day — a median() composite (the right
        # choice for a STATIC scene) would blend a moving patch across
        # several different real-world positions, or wash it out entirely if
        # it only appears in one of several stacked images. mosaic() instead
        # shows where the mass actually was on its most recent clear sighting.
        .sort("system:time_start", False)
    )

    # Composite clipped to ocean-only area — eliminates inland detections.
    # VERIFY against current Earth Engine docs if this ever misbehaves:
    # ImageCollection.sort(property, ascending) and .mosaic() (composites by
    # painting each image in collection order, image N filling only the
    # pixels image N-1 left masked) are long-stable parts of the API, but EE
    # methods do change across versions.
    composite = collection.select(["FAI", "NDVI", "OBS_T"]).mosaic().updateMask(ocean_mask)

    # FAI relative to the surrounding water: a fixed cut on raw FAI trips on
    # haze and sun-glint gradients. Background = local median FAI.
    # Measured on real DR scenes: a 500 m median kept detection at ~10 s and
    # removed a 32 km² shallow-channel false positive near Saona; 2 km
    # windows (median or mean, any grid) took 40-180 s or timed out. Inside a
    # mat wider than ~1 km the background rises with the mat, so only its
    # edges are flagged; typical windrows are far narrower.
    fai = composite.select("FAI")
    background = fai.focalMedian(radius=config.FAI_BG_RADIUS_M, kernelType="square", units="meters")
    fai_anom = fai.subtract(background).rename("FAI_ANOM")

    algae = (
        fai_anom.gt(config.FAI_THRESHOLD)
        .And(composite.select("NDVI").gt(config.NDVI_MIN))
    ).selfMask().rename("algae")

    # Vectorize the binary mask into polygons (ocean area only). The extra
    # bands are reduced per polygon: latest observation time (max) and mean
    # FAI anomaly, used for the covered-area estimate.
    vectors = algae.addBands(composite.select("OBS_T")).addBands(fai_anom).reduceToVectors(
        reducer=ee.Reducer.max().combine(ee.Reducer.mean().unweighted(), sharedInputs=False),
        geometry=aoi,
        scale=config.DETECT_SCALE_M,
        geometryType="polygon",
        eightConnected=True,
        maxPixels=1e10,
        bestEffort=True,
    )

    # Attach area (km^2) computed in EE so we can filter server-side.
    def _with_area(feature: "ee.Feature") -> "ee.Feature":
        area_km2 = feature.geometry().area(maxError=1).divide(1e6)
        return feature.set("area_km2", area_km2)

    vectors = vectors.map(_with_area).filter(
        ee.Filter.gte("area_km2", config.MIN_PATCH_AREA_KM2)
    )

    gdf = _fc_to_gdf(vectors)
    if gdf.empty:
        logger.info("No sargassum patches detected.")
        return gdf

    # Project to a metric system (3857), calculate centroid, then convert back to lat/lon (4326)
    # to avoid UserWarning and get accurate geographic centroids.
    centroids = gdf.geometry.to_crs(epsg=3857).centroid.to_crs(epsg=4326)
    gdf["centroid_lon"] = centroids.x
    gdf["centroid_lat"] = centroids.y
    gdf["source"] = config.DETECTION_SOURCE
    gdf["observed_at"] = gdf["obs_t_ms"].map(_ms_to_iso)
    gdf["covered_km2"] = [
        covered_area(a, m) for a, m in zip(gdf["area_km2"], gdf["fai_anom_mean"])
    ]
    logger.info("Detected %d patch(es).", len(gdf))
    return gdf[["geometry", "centroid_lat", "centroid_lon", "area_km2", "covered_km2",
                "observed_at", "source"]]


def _ms_to_iso(ms) -> str | None:
    try:
        return dt.datetime.fromtimestamp(float(ms) / 1000.0, dt.timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def covered_area(outline_km2: float, mean_fai_anom) -> float | None:
    """Estimated sargassum-covered area inside an outline.

    Each 60 m pixel is only partly covered; the mean FAI anomaly over the
    full-cover anomaly gives the covered fraction (clipped to 0-1).
    """
    try:
        frac = min(max(float(mean_fai_anom) / config.S2_FAI_FULL_COVER, 0.0), 1.0)
    except (TypeError, ValueError):
        return None
    return float(outline_km2) * frac


def _fc_to_gdf(fc: "ee.FeatureCollection") -> gpd.GeoDataFrame:
    """Convert an EE FeatureCollection to a GeoDataFrame (EPSG:4326).

    Uses a raw getInfo() round-trip for portability across geemap versions.
    For large collections, consider geemap.ee_to_gdf (verify name in your version).
    """
    try:
        info = fc.getInfo()
    except Exception:
        logger.exception("Failed to fetch detection features from Earth Engine.")
        return gpd.GeoDataFrame(
            columns=["geometry", "area_km2"], geometry="geometry", crs="EPSG:4326"
        )

    features = info.get("features", [])
    geometries, areas, obs, anom = [], [], [], []
    for feat in features:
        props = feat.get("properties", {})
        geometries.append(shape(feat["geometry"]))
        areas.append(props.get("area_km2"))
        obs.append(props.get("max"))     # latest OBS_T in the polygon
        anom.append(props.get("mean"))   # mean FAI_ANOM in the polygon

    if not geometries:
        return gpd.GeoDataFrame(
            columns=["geometry", "area_km2"], geometry="geometry", crs="EPSG:4326"
        )

    return gpd.GeoDataFrame(
        {"area_km2": areas, "obs_t_ms": obs, "fai_anom_mean": anom},
        geometry=geometries, crs="EPSG:4326",
    )
