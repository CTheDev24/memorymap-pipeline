from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from shapely import contains_xy
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from trimesh import Trimesh

from .buildings import _transform_shapely_polygon
from .terrain import TerrainSurface


EXPOSED_LAND_TAGS = {
    "natural": ["beach", "sand", "bare_rock", "scree", "shingle", "mud"],
    "surface": ["sand", "fine_gravel", "gravel", "rock"],
    "landuse": ["quarry"],
}


def download_exposed_land_polygons(
    bbox: tuple[float, float, float, float],
    center_lat: float,
    center_lon: float,
    transform: dict,
    map_width_mm: float,
    map_height_mm: float,
    *,
    radius_m: float | None = None,
    landcover_file: str | Path | None = None,
) -> list[Polygon]:
    """Load beach, sand, bare-rock, and similar exposed-ground polygons."""
    try:
        import geopandas as gpd

        if landcover_file is not None:
            data = gpd.read_file(landcover_file)
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
                    (center_lat, center_lon),
                    tags=EXPOSED_LAND_TAGS,
                    dist=radius_m,
                )
            elif fetch_bbox is not None:
                south, north, west, east = bbox
                try:
                    data = fetch_bbox(
                        (west, south, east, north),
                        tags=EXPOSED_LAND_TAGS,
                    )
                except TypeError:
                    data = fetch_bbox(
                        north,
                        south,
                        east,
                        west,
                        tags=EXPOSED_LAND_TAGS,
                    )
            else:
                raise RuntimeError("Installed OSMnx does not expose a feature query API")
    except Exception as exc:
        logging.warning("Failed to load exposed-land polygons: %s", exc)
        return []

    print_bounds = box(0.0, 0.0, map_width_mm, map_height_mm)
    transformed: list[Polygon] = []
    for _, feature in data.iterrows():
        geometry = feature.geometry
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
            if print_polygon.geom_type == "Polygon" and not print_polygon.is_empty:
                transformed.append(print_polygon)
            elif print_polygon.geom_type == "MultiPolygon":
                transformed.extend(
                    child for child in print_polygon.geoms if not child.is_empty
                )
    return transformed


def landscape_surface_region(
    width_mm: float,
    height_mm: float,
    margin_mm: float,
    *,
    water_geometries: list[BaseGeometry] = (),
    exposed_geometries: list[BaseGeometry] = (),
) -> BaseGeometry:
    """Return the printable land region that receives the green surface skin."""
    if min(width_mm, height_mm) <= 0 or margin_mm < 0:
        raise ValueError("Landscape dimensions must be positive")
    if min(width_mm, height_mm) <= 2.0 * margin_mm:
        raise ValueError("Landscape margin is too large for the print dimensions")
    region: BaseGeometry = box(
        margin_mm,
        margin_mm,
        width_mm - margin_mm,
        height_mm - margin_mm,
    )
    exclusions = [
        geometry
        for geometry in (*water_geometries, *exposed_geometries)
        if geometry is not None and not geometry.is_empty
    ]
    if exclusions:
        region = region.difference(unary_union(exclusions).buffer(0))
    return region.buffer(0)


def build_conformal_surface_skin(
    surface: TerrainSurface,
    region: BaseGeometry,
    *,
    visible_thickness_mm: float = 0.4,
    embed_depth_mm: float = 0.2,
) -> Trimesh | None:
    """Rasterize a supported, terrain-following material skin.

    The raster follows the DEM triangles directly, so its finest boundary detail
    never exceeds the terrain sampling resolution or the configured mesh budget.
    """
    if visible_thickness_mm <= 0 or embed_depth_mm < 0:
        raise ValueError("Surface skin thickness must be positive")
    clipped = region.intersection(box(0.0, 0.0, surface.width_mm, surface.height_mm))
    if clipped.is_empty:
        return None

    rows, columns = surface.heights_mm.shape
    xs = np.linspace(0.0, surface.width_mm, columns)
    ys = np.linspace(surface.height_mm, 0.0, rows)
    grid = np.asarray(
        [(x, y) for y in ys for x in xs],
        dtype=float,
    )
    top = np.column_stack(
        (
            grid,
            surface.heights_mm.reshape(-1) + visible_thickness_mm,
        )
    )
    bottom = np.column_stack(
        (
            grid,
            surface.heights_mm.reshape(-1) - embed_depth_mm,
        )
    )
    layer_size = len(grid)
    selected: list[tuple[int, int, int]] = []
    buffered = clipped.buffer(1e-9)
    for row in range(rows - 1):
        for column in range(columns - 1):
            a = row * columns + column
            b = a + 1
            c = a + columns
            d = c + 1
            for triangle in ((a, c, b), (b, c, d)):
                center = grid[np.asarray(triangle)].mean(axis=0)
                if bool(contains_xy(buffered, center[0], center[1])):
                    selected.append(triangle)
    if not selected:
        return None

    faces: list[tuple[int, int, int]] = []
    edge_counts: dict[tuple[int, int], int] = {}
    for first, second, third in selected:
        faces.append((first, second, third))
        faces.append(
            (
                first + layer_size,
                third + layer_size,
                second + layer_size,
            )
        )
        for start, end in (
            (first, second),
            (second, third),
            (third, first),
        ):
            key = (min(start, end), max(start, end))
            edge_counts[key] = edge_counts.get(key, 0) + 1
    for (start, end), count in edge_counts.items():
        if count != 1:
            continue
        faces.extend(
            (
                (start, end + layer_size, end),
                (start, start + layer_size, end + layer_size),
            )
        )

    mesh = Trimesh(
        vertices=np.vstack((top, bottom)),
        faces=np.asarray(faces, dtype=int),
        process=True,
    )
    mesh.remove_unreferenced_vertices()
    mesh.fix_normals(multibody=True)
    if not mesh.is_watertight:
        raise ValueError("Landscape surface skin is not watertight")
    return mesh
