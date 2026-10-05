"""Natural Earth map data for the cast map, via cartopy.

cartopy downloads Natural Earth layers on first use into its data directory
(cartopy.config["data_dir"]). At sea without internet, run scripts/fetch_map_data.py beforehand;
if a layer still can't be loaded, the map is drawn without it.

Natural Earth geometries are large (a land polygon can be a whole continent), so they are clipped
to the map view before plotting; otherwise every SVG would carry entire coastlines.
"""

import logging
from collections.abc import Iterable

import cartopy.feature as cfeature
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)

# (category, name) of the layers the maps use; detail maps use 10m, the world inset 110m
DETAIL_LAYERS = (("physical", "land"), ("physical", "lakes"), ("physical", "coastline"))
WORLD_LAYERS = (("physical", "land"),)


def _feature(category: str, name: str, scale: str) -> cfeature.NaturalEarthFeature:
    return cfeature.NaturalEarthFeature(category, name, scale)


def clipped_geometries(
    category: str, name: str, scale: str, extent: tuple[float, float, float, float]
) -> list[BaseGeometry] | None:
    """Geometries of a Natural Earth layer clipped to (west, east, south, north), or None if the
    layer can't be loaded (e.g. not downloaded and no network)
    """
    west, east, south, north = extent
    window = box(west, south, east, north)
    try:
        geometries: Iterable[BaseGeometry] = _feature(
            category, name, scale
        ).intersecting_geometries((west, east, south, north))
        clipped = [geometry.intersection(window) for geometry in geometries]
    except Exception as error:  # noqa: BLE001 - network, cache and shapefile errors all mean "no layer"
        logger.warning("Natural Earth %s %s %s unavailable: %s", scale, category, name, error)
        return None
    return [geometry for geometry in clipped if not geometry.is_empty]


def world_geometries(category: str, name: str) -> list[BaseGeometry] | None:
    try:
        return list(_feature(category, name, "110m").geometries())
    except Exception as error:  # noqa: BLE001
        logger.warning("Natural Earth 110m %s %s unavailable: %s", category, name, error)
        return None


def prefetch() -> list[str]:
    """Downloads every layer the maps use into cartopy's data directory"""
    fetched = []
    for scale, layers in (("10m", DETAIL_LAYERS), ("110m", WORLD_LAYERS)):
        for category, name in layers:
            # Touching the geometries triggers cartopy's download and caches the shapefile
            next(iter(_feature(category, name, scale).geometries()), None)
            fetched.append(f"{scale} {category} {name}")
    return fetched
