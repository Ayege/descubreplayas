"""Detection step: fetch satellite imagery from Earth Engine, cloud-mask, compute FAI/NDVI, and emit sargassum patch polygons."""

from __future__ import annotations

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


def _mask_clouds(image: "ee.Image") -> "ee.Image":
    """Mask clouds/cirrus/shadow using the SCL band, and keep only water pixels.

    SCL classes (Sentinel-2 L2A):
      3=cloud shadow, 6=water, 8=cloud medium prob, 9=cloud high prob,
      10=thin cirrus, 11=snow/ice.
    We keep water (6) and drop the cloud/shadow/cirrus/snow classes.
    """
    scl = image.select("SCL")
    water = scl.eq(6)
    bad = scl.eq(3).Or(scl.eq(8)).Or(scl.eq(9)).Or(scl.eq(10)).Or(scl.eq(11))
    mask = water.And(bad.Not())
    return image.updateMask(mask)


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

    logger.info("Detecting sargassum %s..%s over EEZ.", start, end)

    collection = (
        ee.ImageCollection(_S2_COLLECTION)
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", config.MAX_CLOUD_COVER_PCT))
        .map(_mask_clouds)
        .map(_add_indices)
    )

    # Median composite over the period reduces residual cloud/noise.
    composite = collection.select(["FAI", "NDVI"]).median().clip(aoi)

    algae = (
        composite.select("FAI").gt(config.FAI_THRESHOLD)
        .And(composite.select("NDVI").gt(config.NDVI_MIN))
    ).selfMask().rename("algae")

    # Vectorize the binary mask into polygons.
    vectors = algae.reduceToVectors(
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
    logger.info("Detected %d patch(es).", len(gdf))
    return gdf[["geometry", "centroid_lat", "centroid_lon", "area_km2", "source"]]


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
    geometries = []
    areas = []
    for feat in features:
        geometries.append(shape(feat["geometry"]))
        areas.append(feat.get("properties", {}).get("area_km2"))

    if not geometries:
        return gpd.GeoDataFrame(
            columns=["geometry", "area_km2"], geometry="geometry", crs="EPSG:4326"
        )

    return gpd.GeoDataFrame(
        {"area_km2": areas}, geometry=geometries, crs="EPSG:4326"
    )
