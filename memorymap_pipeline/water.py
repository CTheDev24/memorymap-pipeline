from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.geometry import box as shapely_box
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, split, unary_union
from shapely.strtree import STRtree
from trimesh import Trimesh
from trimesh.creation import triangulate_polygon
from trimesh.util import concatenate

from .buildings import _transform_shapely_polygon
from .mesh import route_mesh_from_polygon
from .projection import apply_transform, project_lonlat_array
from .terrain import TerrainSurface, terrain_mesh_axes, terrain_mesh_heights

WATER_TAGS = {
    "natural": ["water", "coastline"],
    "water": ["ocean", "sea", "bay", "strait", "lagoon", "fjord", "sound"],
    "waterway": ["riverbank", "river", "stream", "canal", "drain", "ditch"],
    "landuse": ["reservoir", "basin"],
    "place": ["sea", "ocean", "bay"],
}

LINEAR_WATERWAY_WIDTHS_MM = {
    "river": 1.6,
    "canal": 1.2,
    "stream": 0.8,
    "drain": 0.8,
    "ditch": 0.8,
}


@dataclass(frozen=True)
class WaterFeature:
    """Print-space water geometry with enough provenance for surface styling."""

    geometry: BaseGeometry
    kind: str
    waterway: str | None = None


@dataclass(frozen=True)
class WaterBody:
    geometry: Polygon
    level_mm: float


def download_water_polygons(
    bbox: tuple[float, float, float, float],
    center_lat: float,
    center_lon: float,
    transform: dict,
    map_width_mm: float,
    map_height_mm: float,
    radius_m: float | None = None,
    water_file: str | Path | None = None,
    minimum_waterway_width_mm: float = 0.8,
    waterway_widths_mm: dict[str, float] | None = None,
    include_metadata: bool = False,
) -> list[BaseGeometry] | list[WaterFeature]:
    """Load OSM water areas and transform them into clipped print-space polygons."""
    if minimum_waterway_width_mm <= 0:
        raise ValueError("Minimum waterway width must be positive")
    configured_widths = dict(LINEAR_WATERWAY_WIDTHS_MM)
    if waterway_widths_mm:
        configured_widths.update(waterway_widths_mm)
    if any(width <= 0 for width in configured_widths.values()):
        raise ValueError("Waterway widths must be positive")
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
                    data = fetch_bbox(
                        (lon_min, lat_min, lon_max, lat_max), tags=WATER_TAGS
                    )
                except TypeError:
                    data = fetch_bbox(
                        lat_max, lat_min, lon_max, lon_min, tags=WATER_TAGS
                    )
            else:
                raise RuntimeError("Installed OSMnx does not expose a feature query API")
    except Exception as exc:
        logging.warning("Failed to load water polygons and coastlines: %s", exc)
        return []

    print_bounds = shapely_box(0.0, 0.0, map_width_mm, map_height_mm)
    transformed: list[WaterFeature] = []
    transformed_coastlines: list[LineString] = []
    for _, feature in data.iterrows():
        geometry = feature.geometry
        if geometry is None or geometry.is_empty:
            continue
        if feature.get("natural") == "coastline":
            transformed_coastlines.extend(
                _transform_coastline_geometry(
                    geometry,
                    center_lat=center_lat,
                    center_lon=center_lon,
                    transform=transform,
                    print_bounds=print_bounds,
                )
            )
            continue
        waterway = feature.get("waterway")
        if not isinstance(waterway, str):
            waterway = None
        if waterway in configured_widths:
            width_mm = max(
                minimum_waterway_width_mm,
                configured_widths[waterway],
            )
            for line in _transform_linear_geometry(
                geometry,
                center_lat=center_lat,
                center_lon=center_lon,
                transform=transform,
                print_bounds=print_bounds,
            ):
                buffered = line.buffer(
                    width_mm / 2.0,
                    cap_style="round",
                    join_style="round",
                ).intersection(print_bounds)
                if not buffered.is_empty:
                    transformed.append(
                        WaterFeature(buffered, kind="waterway", waterway=waterway)
                    )
        for part in _polygon_geometry_parts(geometry):
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
                transformed.append(WaterFeature(print_polygon, kind="area"))
    transformed.extend(
        WaterFeature(region, kind="ocean")
        for region in _infer_coastal_water_regions(
            transformed_coastlines,
            print_bounds,
        )
    )
    if include_metadata:
        return transformed
    return [feature.geometry for feature in transformed]


def _polygon_geometry_parts(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
        parts: list[Polygon] = []
        for part in geometry.geoms:
            parts.extend(_polygon_geometry_parts(part))
        return parts
    return []


def _line_geometry_parts(geometry: BaseGeometry) -> list[LineString]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "LineString":
        return [geometry]
    if geometry.geom_type in ("MultiLineString", "GeometryCollection"):
        parts: list[LineString] = []
        for part in geometry.geoms:
            parts.extend(_line_geometry_parts(part))
        return parts
    return []


def _transform_linear_geometry(
    geometry: BaseGeometry,
    *,
    center_lat: float,
    center_lon: float,
    transform: dict,
    print_bounds: Polygon,
) -> list[LineString]:
    """Transform and clip line components without applying coastline semantics."""
    transformed: list[LineString] = []
    for part in _line_geometry_parts(geometry):
        coordinates = np.asarray(part.coords, dtype=float)
        if coordinates.shape[0] < 2:
            continue
        projected = project_lonlat_array(
            coordinates[:, 1],
            coordinates[:, 0],
            center_lat=center_lat,
            center_lon=center_lon,
        )
        frame = transform.get("map_frame")
        transformed_points = (
            frame.transform_projected(projected)
            if frame is not None
            else apply_transform(projected, transform)
        )
        clipped = LineString(
            [(float(x), float(y)) for x, y in transformed_points]
        ).intersection(print_bounds)
        transformed.extend(_line_geometry_parts(clipped))
    return transformed


def _transform_coastline_geometry(
    geometry: BaseGeometry,
    *,
    center_lat: float,
    center_lon: float,
    transform: dict,
    print_bounds: Polygon,
) -> list[LineString]:
    transformed: list[LineString] = []
    parts = (
        list(geometry.geoms)
        if geometry.geom_type in ("MultiLineString", "GeometryCollection")
        else [geometry]
    )
    for part in parts:
        if part is None or part.is_empty:
            continue
        if part.geom_type == "LineString":
            coordinates = np.asarray(part.coords, dtype=float)
            if coordinates.shape[0] < 2:
                continue
            lons = coordinates[:, 0]
            lats = coordinates[:, 1]
            projected = project_lonlat_array(lats, lons, center_lat=center_lat, center_lon=center_lon)
            frame = transform.get("map_frame")
            transformed_points = (
                frame.transform_projected(projected)
                if frame is not None
                else apply_transform(projected, transform)
            )
            line = LineString(
                [(float(x), float(y)) for x, y in transformed_points]
            ).intersection(print_bounds)
            if line.is_empty:
                continue
            if line.geom_type == "LineString":
                transformed.append(line)
            elif line.geom_type == "MultiLineString":
                transformed.extend(
                    segment for segment in line.geoms if not segment.is_empty
                )
        elif part.geom_type in ("MultiLineString", "GeometryCollection"):
            transformed.extend(
                _transform_coastline_geometry(
                    part,
                    center_lat=center_lat,
                    center_lon=center_lon,
                    transform=transform,
                    print_bounds=print_bounds,
                )
            )
    return transformed


def _infer_coastal_water_regions(
    coastlines: list[LineString],
    print_bounds: Polygon,
) -> list[Polygon]:
    if not coastlines:
        return []
    merged_lines = [line for line in coastlines if line.length > 1e-6]
    if not merged_lines:
        return []
    boundary = print_bounds.boundary
    try:
        divider = unary_union(merged_lines)
        regions = [
            region
            for region in split(print_bounds, divider).geoms
            if region.geom_type == "Polygon"
        ]
        if len(regions) <= 1:
            regions = list(polygonize(unary_union([boundary, divider])))
    except Exception:
        return []
    if len(regions) <= 1:
        return []
    sea_hint_parts = []
    land_hint_parts = []
    for line in merged_lines:
        coordinates = list(line.coords)
        for start, end in zip(coordinates, coordinates[1:]):
            segment = LineString([start, end])
            if segment.length <= 1e-8:
                continue
            try:
                sea_hint = segment.buffer(
                    -0.3,
                    single_sided=True,
                    cap_style=2,
                    join_style=2,
                )
                land_hint = segment.buffer(
                    0.3,
                    single_sided=True,
                    cap_style=2,
                    join_style=2,
                )
            except Exception:
                continue
            if not sea_hint.is_empty:
                sea_hint_parts.append(sea_hint)
            if not land_hint.is_empty:
                land_hint_parts.append(land_hint)
    sea_hint = unary_union(sea_hint_parts) if sea_hint_parts else None
    land_hint = unary_union(land_hint_parts) if land_hint_parts else None
    if sea_hint is None or land_hint is None:
        return []
    inferred = []
    for region in regions:
        if region.is_empty or region.area <= 1e-6:
            continue
        if region.boundary.intersection(boundary).length <= 0.1:
            continue
        sea_evidence = sea_hint.intersection(region).area
        land_evidence = land_hint.intersection(region).area
        if sea_evidence > 1e-8 and sea_evidence > land_evidence:
            inferred.append(region)
    return inferred


def prepare_water_bodies(
    geometries: list[BaseGeometry],
    surface: TerrainSurface,
    recess_mm: float = 0.4,
    surface_offset_mm: float = 0.0,
    minimum_height_mm: float = -0.8,
    shoreline_tolerance_mm: float = 0.1,
    margin_mm: float = 0.0,
) -> list[WaterBody]:
    """Merge connected vector water polygons and assign one level to each body."""
    if recess_mm < 0 or shoreline_tolerance_mm < 0 or margin_mm < 0:
        raise ValueError("Water recess, tolerance, and margin cannot be negative")
    valid = [geometry for geometry in geometries if geometry is not None and not geometry.is_empty]
    if not valid:
        return []
    if surface.width_mm <= 2 * margin_mm or surface.height_mm <= 2 * margin_mm:
        raise ValueError("Water margin is too large for the terrain dimensions")
    plate = shapely_box(
        margin_mm,
        margin_mm,
        surface.width_mm - margin_mm,
        surface.height_mm - margin_mm,
    )
    edge_inset = max(0.01, shoreline_tolerance_mm / 2.0)
    water_clip = plate.buffer(-edge_inset, join_style="mitre")
    merged = unary_union(valid).buffer(0).intersection(water_clip)
    if shoreline_tolerance_mm:
        merged = merged.simplify(shoreline_tolerance_mm, preserve_topology=True)
    parts = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
    bodies: list[WaterBody] = []
    for part in parts:
        if part.geom_type != "Polygon" or part.is_empty or part.area <= 1e-8:
            continue
        coordinates = np.asarray(part.exterior.coords, dtype=float)
        boundary_heights = surface.sample(coordinates[:, 0], coordinates[:, 1])
        level = max(
            minimum_height_mm,
            float(np.min(boundary_heights)) + surface_offset_mm - recess_mm,
        )
        bodies.append(WaterBody(part, level))
    return bodies


def build_vector_water_mesh(
    water_bodies: list[WaterBody],
    thickness_mm: float = 0.6,
) -> Trimesh | None:
    """Build water solids with 0.2 mm exposed above their embedded support."""
    if thickness_mm <= 0:
        raise ValueError("Water mesh thickness must be positive")
    meshes = []
    for body in water_bodies:
        try:
            meshes.append(
                route_mesh_from_polygon(
                    body.geometry,
                    height_mm=thickness_mm,
                    z_offset=body.level_mm - thickness_mm,
                )
            )
        except Exception as exc:
            logging.warning("Skipping invalid water polygon during extrusion: %s", exc)
    if not meshes:
        return None
    return meshes[0] if len(meshes) == 1 else concatenate(meshes)


def _polygon_parts(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return [part for part in geometry.geoms if not part.is_empty]
    return []


def build_terrain_mesh_with_water(
    surface: TerrainSurface,
    base_thickness_mm: float,
    water_bodies: list[WaterBody],
    water_mesh_thickness_mm: float = 0.6,
    support_overlap_mm: float = 0.4,
    flat_margin_mm: float = 0.0,
) -> Trimesh:
    """Create terrain with a recessed support cavity below each water body."""
    if base_thickness_mm <= 0:
        raise ValueError("Terrain base thickness must be positive")
    if water_mesh_thickness_mm <= 0:
        raise ValueError("Water mesh thickness must be positive")
    if not 0 <= support_overlap_mm < water_mesh_thickness_mm:
        raise ValueError("Water support overlap must be smaller than mesh thickness")
    plate = shapely_box(0.0, 0.0, surface.width_mm, surface.height_mm)
    water_union = unary_union([body.geometry for body in water_bodies])
    support_levels = {
        id(body): body.level_mm - water_mesh_thickness_mm + support_overlap_mm
        for body in water_bodies
    }
    water_tree = STRtree([body.geometry for body in water_bodies])

    def nearby_bodies(geometry: BaseGeometry) -> list[WaterBody]:
        indices = water_tree.query(geometry, predicate="intersects")
        return [water_bodies[int(index)] for index in indices]

    xs, ys = terrain_mesh_axes(surface, flat_margin_mm)
    rows, columns = len(ys), len(xs)

    def sample_land(x_mm, y_mm):
        return terrain_mesh_heights(
            surface,
            np.asarray(x_mm, dtype=float),
            np.asarray(y_mm, dtype=float),
            flat_margin_mm,
        )

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    water_edges: dict[tuple[tuple[float, float], tuple[float, float]], list] = {}

    def add_region(
        region: BaseGeometry,
        z_value,
        track_water_edges: bool = False,
        reverse: bool = False,
    ) -> None:
        for polygon in _polygon_parts(region):
            points, triangles = triangulate_polygon(polygon)
            start = len(vertices)
            for x, y in points:
                z = float(z_value(x, y)) if callable(z_value) else float(z_value)
                vertices.append((float(x), float(y), z))
            faces.extend(
                tuple(start + int(index) for index in (face[::-1] if reverse else face))
                for face in triangles
            )
            if track_water_edges:
                for triangle in triangles:
                    for first_index, second_index in (
                        (triangle[0], triangle[1]),
                        (triangle[1], triangle[2]),
                        (triangle[2], triangle[0]),
                    ):
                        first = tuple(float(value) for value in points[first_index])
                        second = tuple(float(value) for value in points[second_index])
                        key = tuple(sorted((first, second)))
                        if key in water_edges:
                            water_edges[key][0] += 1
                        else:
                            water_edges[key] = [1, first, second, float(z_value)]

    for row in range(rows - 1):
        for column in range(columns - 1):
            cell = shapely_box(xs[column], ys[row + 1], xs[column + 1], ys[row])
            add_region(cell.difference(water_union), sample_land)
            for body in nearby_bodies(cell):
                add_region(
                    cell.intersection(body.geometry),
                    support_levels[id(body)],
                    True,
                )

    boundary_tolerance = 1e-7
    for count, first, second, level in water_edges.values():
        segment = LineString([first, second])
        if count != 1 or segment.length <= 1e-9:
            continue
        if plate.boundary.buffer(boundary_tolerance).covers(segment):
            continue
        x1, y1 = first
        x2, y2 = second
        land1 = float(sample_land(x1, y1))
        land2 = float(sample_land(x2, y2))
        start = len(vertices)
        vertices.extend(
            (
                (x1, y1, land1),
                (x2, y2, land2),
                (x2, y2, level),
                (x1, y1, level),
            )
        )
        faces.extend(((start, start + 2, start + 1), (start, start + 3, start + 2)))

    perimeter_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    perimeter_segments.extend(((xs[i], 0.0), (xs[i + 1], 0.0)) for i in range(columns - 1))
    perimeter_segments.extend(
        ((surface.width_mm, ys[i]), (surface.width_mm, ys[i + 1])) for i in range(rows - 1)
    )
    perimeter_segments.extend(
        ((xs[i + 1], surface.height_mm), (xs[i], surface.height_mm))
        for i in range(columns - 2, -1, -1)
    )
    perimeter_segments.extend(((0.0, ys[i + 1]), (0.0, ys[i])) for i in range(rows - 2, -1, -1))
    def line_parts(geometry: BaseGeometry) -> list[LineString]:
        if geometry.is_empty:
            return []
        if geometry.geom_type == "LineString":
            return [geometry]
        if geometry.geom_type == "MultiLineString":
            return list(geometry.geoms)
        return []

    def add_outer_wall(line: LineString, top_value) -> None:
        coordinates = list(line.coords)
        if len(coordinates) < 2:
            return
        first, second = coordinates[0], coordinates[-1]
        x1, y1 = first
        x2, y2 = second
        top1 = float(top_value(x1, y1)) if callable(top_value) else float(top_value)
        top2 = float(top_value(x2, y2)) if callable(top_value) else float(top_value)
        start = len(vertices)
        vertices.extend(
            (
                (x1, y1, -base_thickness_mm),
                (x2, y2, -base_thickness_mm),
                (x2, y2, top2),
                (x1, y1, top1),
            )
        )
        faces.extend(((start, start + 1, start + 2), (start, start + 2, start + 3)))

    for first, second in perimeter_segments:
        segment = LineString([first, second])
        for part in line_parts(segment.difference(water_union)):
            add_outer_wall(part, sample_land)
        for body in nearby_bodies(segment):
            for part in line_parts(segment.intersection(body.geometry)):
                add_outer_wall(part, support_levels[id(body)])

    for row in range(rows - 1):
        for column in range(columns - 1):
            cell = shapely_box(xs[column], ys[row + 1], xs[column + 1], ys[row])
            add_region(
                cell.difference(water_union),
                -base_thickness_mm,
                reverse=True,
            )
            for body in nearby_bodies(cell):
                add_region(
                    cell.intersection(body.geometry),
                    -base_thickness_mm,
                    reverse=True,
                )
    mesh = Trimesh(np.asarray(vertices), np.asarray(faces), process=True)
    mesh.remove_unreferenced_vertices()
    # The terrain partition is assembled from independently triangulated land,
    # water-support, bottom, and wall regions. Their local winding can disagree
    # even when every edge is closed, which slicers interpret as internal voids
    # and enormous bridge floors. Orient the completed watertight shell outward.
    mesh.fix_normals(multibody=True)
    return mesh
