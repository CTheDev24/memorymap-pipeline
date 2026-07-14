from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon, box as shapely_box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from trimesh import Trimesh
from trimesh.util import concatenate

from .buildings import _transform_shapely_polygon
from .mesh import route_mesh_from_polygon
from .terrain import TerrainSurface, drape_mesh


# Polygon-only OSM tags with consistent area semantics. Ambiguous tags such as
# natural=bare_rock, landuse=farmland, and surface=unpaved are intentionally omitted.
LANDSCAPE_TAGS = {
    "natural": ["wood", "scrub", "heath", "grassland", "beach", "sand"],
    "landuse": ["forest", "grass", "meadow", "orchard", "vineyard", "allotments"],
    "leisure": ["park", "garden", "nature_reserve", "golf_course"],
    "surface": "sand",
}

VEGETATION_TAGS = {
    "natural": {"wood", "scrub", "heath", "grassland"},
    "landuse": {"forest", "grass", "meadow", "orchard", "vineyard", "allotments"},
    "leisure": {"park", "garden", "nature_reserve", "golf_course"},
}

SAND_TAGS = {
    "natural": {"beach", "sand"},
    "surface": {"sand"},
}


def _has_tag(properties: Any, accepted: dict[str, set[str]]) -> bool:
    for key, values in accepted.items():
        value = properties.get(key)
        if value is not None and str(value).lower() in values:
            return True
    return False


def download_landscape_polygons(
    bbox: tuple[float, float, float, float],
    center_lat: float,
    center_lon: float,
    transform: dict,
    map_width_mm: float,
    map_height_mm: float,
    radius_m: float | None = None,
    landscape_file: str | Path | None = None,
) -> dict[str, list[BaseGeometry]]:
    """Load tagged OSM vegetation and sand polygons into clipped print space."""
    try:
        import geopandas as gpd

        if landscape_file is not None:
            data = gpd.read_file(landscape_file)
        else:
            import osmnx as ox

            fetch_point = getattr(ox, "features_from_point", None) or getattr(
                ox, "geometries_from_point", None
            )
            fetch_bbox = getattr(ox, "features_from_bbox", None) or getattr(
                ox, "geometries_from_bbox", None
            )
            if radius_m is not None and fetch_point is not None:
                data = fetch_point(
                    (center_lat, center_lon), tags=LANDSCAPE_TAGS, dist=radius_m
                )
            elif fetch_bbox is not None:
                south, north, west, east = bbox
                try:
                    data = fetch_bbox((west, south, east, north), tags=LANDSCAPE_TAGS)
                except TypeError:
                    data = fetch_bbox(north, south, east, west, tags=LANDSCAPE_TAGS)
            else:
                raise RuntimeError("Installed OSMnx does not expose a feature query API")
    except Exception as exc:
        logging.warning("Failed to load landscape polygons: %s", exc)
        return {"vegetation": [], "sand": []}

    print_bounds = shapely_box(0.0, 0.0, map_width_mm, map_height_mm)
    result: dict[str, list[BaseGeometry]] = {"vegetation": [], "sand": []}
    for _index, row in data.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            continue
        # Sand takes precedence for features carrying both a general park/land-use
        # tag and an explicit beach/sand tag.
        category = "sand" if _has_tag(row, SAND_TAGS) else (
            "vegetation" if _has_tag(row, VEGETATION_TAGS) else None
        )
        if category is None:
            continue
        parts = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        for part in parts:
            if part.geom_type != "Polygon":
                continue
            try:
                polygon = _transform_shapely_polygon(
                    part,
                    center_lat=center_lat,
                    center_lon=center_lon,
                    transform=transform,
                ).intersection(print_bounds)
            except Exception:
                continue
            if not polygon.is_empty:
                result[category].append(polygon)
    return result


def _polygon_parts(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return [part for part in geometry.geoms if not part.is_empty]
    return []


def build_landscape_surface_mesh(
    geometries: list[BaseGeometry],
    surface: TerrainSurface,
    thickness_mm: float = 0.2,
    margin_mm: float = 0.0,
) -> Trimesh | None:
    """Create a thin, terrain-following material skin within the print margin."""
    if thickness_mm <= 0 or margin_mm < 0:
        raise ValueError("Landscape thickness must be positive and margin non-negative")
    if surface.width_mm <= 2 * margin_mm or surface.height_mm <= 2 * margin_mm:
        raise ValueError("Landscape margin is too large for the terrain dimensions")
    valid = [geometry for geometry in geometries if geometry is not None and not geometry.is_empty]
    if not valid:
        return None
    printable = shapely_box(
        margin_mm,
        margin_mm,
        surface.width_mm - margin_mm,
        surface.height_mm - margin_mm,
    )
    merged = unary_union(valid).buffer(0).intersection(printable)
    meshes = [
        drape_mesh(
            route_mesh_from_polygon(part, thickness_mm, -thickness_mm / 2.0),
            surface,
        )
        for part in _polygon_parts(merged)
        if part.area > 1e-8
    ]
    if not meshes:
        return None
    return meshes[0] if len(meshes) == 1 else concatenate(meshes)
