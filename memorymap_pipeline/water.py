from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from shapely import contains_xy
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from trimesh import Trimesh

from .terrain import TerrainSurface
from .buildings import _transform_shapely_polygon


WATER_TAGS = {
    "natural": "water",
    "waterway": "riverbank",
    "landuse": ["reservoir", "basin"],
}


def download_water_polygons(
    bbox: tuple[float, float, float, float],
    center_lat: float,
    center_lon: float,
    transform: dict,
    map_width_mm: float,
    map_height_mm: float,
    radius_m: float | None = None,
    water_file: str | Path | None = None,
) -> list[BaseGeometry]:
    """Load OSM water areas and transform them into clipped print-space polygons."""
    try:
        import geopandas as gpd

        if water_file is not None:
            data = gpd.read_file(water_file)
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
                    (center_lat, center_lon), tags=WATER_TAGS, dist=radius_m
                )
            elif fetch_bbox is not None:
                lat_min, lat_max, lon_min, lon_max = bbox
                try:
                    data = fetch_bbox((lon_min, lat_min, lon_max, lat_max), tags=WATER_TAGS)
                except TypeError:
                    data = fetch_bbox(lat_max, lat_min, lon_max, lon_min, tags=WATER_TAGS)
            else:
                raise RuntimeError("Installed OSMnx does not expose a feature query API")
    except Exception as exc:
        logging.warning("Failed to load water polygons: %s", exc)
        return []

    from shapely.geometry import box as shapely_box

    print_bounds = shapely_box(0.0, 0.0, map_width_mm, map_height_mm)
    transformed: list[BaseGeometry] = []
    for geometry in data.geometry:
        if geometry is None or geometry.is_empty:
            continue
        parts = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        for part in parts:
            if part.geom_type != "Polygon":
                continue
            try:
                print_polygon = _transform_shapely_polygon(
                    part,
                    center_lat=center_lat,
                    center_lon=center_lon,
                    transform=transform,
                ).intersection(print_bounds)
            except Exception:
                continue
            if not print_polygon.is_empty:
                transformed.append(print_polygon)
    return transformed


def rasterize_water_mask(
    geometries: list[BaseGeometry],
    surface: TerrainSurface,
) -> np.ndarray:
    """Return a terrain-vertex mask for water polygons in print-space millimeters."""
    valid = [geometry for geometry in geometries if geometry is not None and not geometry.is_empty]
    if not valid:
        return np.zeros(surface.heights_mm.shape, dtype=bool)
    water = unary_union(valid)
    rows, columns = surface.heights_mm.shape
    xs = np.linspace(0.0, surface.width_mm, columns)
    ys = np.linspace(surface.height_mm, 0.0, rows)
    x_grid, y_grid = np.meshgrid(xs, ys)
    return contains_xy(water, x_grid, y_grid) | contains_xy(water.buffer(1e-9), x_grid, y_grid)


def recess_water_surface(
    surface: TerrainSurface,
    water_mask: np.ndarray,
    recess_mm: float = 0.4,
    minimum_height_mm: float = -0.8,
) -> TerrainSurface:
    """Lower water samples while preserving the terrain analysis and structural floor."""
    mask = np.asarray(water_mask, dtype=bool)
    if mask.shape != surface.heights_mm.shape:
        raise ValueError("Water mask dimensions must match the terrain grid")
    if recess_mm < 0:
        raise ValueError("Water recess cannot be negative")
    heights = surface.heights_mm.copy()
    heights[mask] = np.maximum(minimum_height_mm, heights[mask] - recess_mm)
    return TerrainSurface(heights, surface.width_mm, surface.height_mm, surface.analysis)


def build_water_mesh(
    recessed_surface: TerrainSurface,
    water_mask: np.ndarray,
    embed_depth_mm: float = 0.2,
) -> Trimesh | None:
    """Build gray water cells embedded below the already-recessed terrain surface."""
    mask = np.asarray(water_mask, dtype=bool)
    if mask.shape != recessed_surface.heights_mm.shape:
        raise ValueError("Water mask dimensions must match the terrain grid")
    if embed_depth_mm <= 0:
        raise ValueError("Water embed depth must be positive")
    rows, columns = mask.shape
    xs = np.linspace(0.0, recessed_surface.width_mm, columns)
    ys = np.linspace(recessed_surface.height_mm, 0.0, rows)
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    for row in range(rows - 1):
        for column in range(columns - 1):
            cell_mask = mask[row : row + 2, column : column + 2]
            if not cell_mask.any():
                continue
            top_z = float(np.mean(recessed_surface.heights_mm[row : row + 2, column : column + 2]))
            bottom_z = top_z - embed_depth_mm
            x0, x1 = xs[column], xs[column + 1]
            y1, y0 = ys[row], ys[row + 1]
            start = len(vertices)
            vertices.extend(
                (
                    (x0, y0, bottom_z),
                    (x1, y0, bottom_z),
                    (x1, y1, bottom_z),
                    (x0, y1, bottom_z),
                    (x0, y0, top_z),
                    (x1, y0, top_z),
                    (x1, y1, top_z),
                    (x0, y1, top_z),
                )
            )
            faces.extend(
                tuple(start + index for index in face)
                for face in (
                    (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
                    (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
                    (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
                )
            )
    if not vertices:
        return None
    return Trimesh(
        vertices=np.asarray(vertices, dtype=float),
        faces=np.asarray(faces, dtype=int),
        process=True,
    )
