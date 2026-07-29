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
from .terrain import TerrainSurface, terrain_mesh_heights


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


def _oriented_boundary_edges(triangles: np.ndarray) -> np.ndarray:
    """Return oriented edges used by exactly one triangle."""
    oriented = np.concatenate(
        (
            triangles[:, [0, 1]],
            triangles[:, [1, 2]],
            triangles[:, [2, 0]],
        ),
        axis=0,
    )
    undirected = np.sort(oriented, axis=1)
    _, inverse, counts = np.unique(
        undirected,
        axis=0,
        return_inverse=True,
        return_counts=True,
    )
    return oriented[counts[inverse] == 1]


def _split_pinched_boundary_vertices(
    triangles: np.ndarray,
    grid: np.ndarray,
    heights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Duplicate only boundary vertices that contain multiple triangle fans.

    A normal manifold boundary has two incident boundary edges per vertex.
    Diagonal raster contacts create four or more. Splitting those rare vertices
    by their local edge-connected triangle fans prevents non-manifold vertical
    walls without graph-walking every terrain vertex.
    """
    remapped = np.asarray(triangles, dtype=int).copy()
    boundary = _oriented_boundary_edges(remapped)
    boundary_vertices, boundary_degree = np.unique(
        boundary.reshape(-1),
        return_counts=True,
    )
    pinched = set(boundary_vertices[boundary_degree > 2].tolist())
    if not pinched:
        return remapped, grid, heights

    occurrences = np.argwhere(np.isin(remapped, list(pinched)))
    by_vertex: dict[int, list[tuple[int, int]]] = {}
    for triangle_index, corner_index in occurrences:
        global_index = int(remapped[triangle_index, corner_index])
        by_vertex.setdefault(global_index, []).append(
            (int(triangle_index), int(corner_index))
        )

    duplicate_sources: list[int] = []
    original_vertex_count = len(grid)
    for global_index, vertex_occurrences in by_vertex.items():
        triangle_indices = {triangle_index for triangle_index, _ in vertex_occurrences}
        neighbours = {triangle_index: set() for triangle_index in triangle_indices}
        edge_members: dict[int, list[int]] = {}
        for triangle_index in triangle_indices:
            for other_index in remapped[triangle_index]:
                if other_index != global_index:
                    edge_members.setdefault(int(other_index), []).append(triangle_index)
        for members in edge_members.values():
            for member in members:
                neighbours[member].update(other for other in members if other != member)

        fans: list[set[int]] = []
        remaining = set(triangle_indices)
        while remaining:
            seed = remaining.pop()
            fan = {seed}
            pending = [seed]
            while pending:
                current = pending.pop()
                connected = neighbours[current].intersection(remaining)
                remaining.difference_update(connected)
                fan.update(connected)
                pending.extend(connected)
            fans.append(fan)

        for fan in fans[1:]:
            replacement = original_vertex_count + len(duplicate_sources)
            duplicate_sources.append(global_index)
            for triangle_index, corner_index in vertex_occurrences:
                if triangle_index in fan:
                    remapped[triangle_index, corner_index] = replacement

    if duplicate_sources:
        source_indices = np.asarray(duplicate_sources, dtype=int)
        grid = np.vstack((grid, grid[source_indices]))
        heights = np.concatenate((heights, heights[source_indices]))
    return remapped, grid, heights


def build_conformal_surface_skin(
    surface: TerrainSurface,
    region: BaseGeometry,
    *,
    visible_thickness_mm: float = 0.4,
    embed_depth_mm: float = 0.2,
    clip_region: BaseGeometry | None = None,
    flat_margin_mm: float = 0.0,
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

    source_rows, source_columns = surface.heights_mm.shape
    xs = np.linspace(0.0, surface.width_mm, source_columns)
    ys = np.linspace(surface.height_mm, 0.0, source_rows)
    clip = None
    if clip_region is not None:
        clip = clip_region.intersection(
            box(0.0, 0.0, surface.width_mm, surface.height_mm)
        )
        if clip.is_empty:
            return None
        minimum_x, minimum_y, maximum_x, maximum_y = clip.bounds
        xs = np.unique(np.append(xs, (minimum_x, maximum_x)))
        ys = np.unique(np.append(ys, (minimum_y, maximum_y)))[::-1]
    rows, columns = len(ys), len(xs)
    grid = np.asarray(
        [(x, y) for y in ys for x in xs],
        dtype=float,
    )
    buffered = clipped.buffer(1e-9)
    cell_rows = np.repeat(np.arange(rows - 1, dtype=int), columns - 1)
    cell_columns = np.tile(np.arange(columns - 1, dtype=int), rows - 1)
    first = cell_rows * columns + cell_columns
    second = first + 1
    third = first + columns
    fourth = third + 1
    triangles = np.empty((len(first) * 2, 3), dtype=int)
    triangles[0::2] = np.column_stack((first, third, second))
    triangles[1::2] = np.column_stack((second, third, fourth))
    centers = grid[triangles].mean(axis=1)
    selected_mask = np.asarray(
        contains_xy(buffered, centers[:, 0], centers[:, 1]),
        dtype=bool,
    )
    if clip is not None:
        selected_mask &= np.asarray(
            contains_xy(
                clip.buffer(1e-9),
                centers[:, 0],
                centers[:, 1],
            ),
            dtype=bool,
        )
    selected = triangles[selected_mask]
    if len(selected) == 0:
        return None

    height_values = np.asarray(
        terrain_mesh_heights(
            surface,
            grid[:, 0],
            grid[:, 1],
            flat_margin_mm,
        ),
        dtype=float,
    )
    selected, expanded_grid, expanded_heights = _split_pinched_boundary_vertices(
        selected,
        grid,
        height_values,
    )
    used_indices, local_indices = np.unique(selected, return_inverse=True)
    local_triangles = local_indices.reshape((-1, 3))
    selected_grid = expanded_grid[used_indices]
    selected_heights = expanded_heights[used_indices]
    top = np.column_stack(
        (selected_grid, selected_heights + visible_thickness_mm)
    )
    bottom = np.column_stack(
        (selected_grid, selected_heights - embed_depth_mm)
    )
    layer_size = len(selected_grid)
    bottom_faces = local_triangles[:, [0, 2, 1]] + layer_size
    boundary = _oriented_boundary_edges(local_triangles)
    start = boundary[:, 0]
    end = boundary[:, 1]
    side_faces = np.vstack(
        (
            np.column_stack((start, end + layer_size, end)),
            np.column_stack((start, start + layer_size, end + layer_size)),
        )
    )
    mesh = Trimesh(
        vertices=np.vstack((top, bottom)),
        faces=np.vstack((local_triangles, bottom_faces, side_faces)),
        process=False,
    )
    if not mesh.is_watertight:
        raise ValueError("Landscape surface skin is not watertight")
    return mesh
