from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Callable

import numpy as np
import shapely.geometry as geom
import shapely.ops as ops
from shapely.ops import triangulate
from shapely.strtree import STRtree
from trimesh import Trimesh

from .building_classification import BuildingClass, classify_building, preset_for
from .geometry import repair_polygon
from .landmarks import (
    LandmarkDefinition,
    LandmarkRegistryError,
    load_default_landmark_registry,
)
from .mesh import route_mesh_from_polygon
from .projection import apply_transform, project_lonlat_array
from .stadiums import StadiumRecipe, build_stadium_mesh


SUPPORTED_ROOF_SHAPES = {"flat", "gabled", "hipped", "pyramidal", "skillion"}
MIN_PLAUSIBLE_LEVEL_HEIGHT_M = 1.5


@dataclass(frozen=True)
class BuildingDimensions:
    min_height_m: float
    total_height_m: float
    roof_height_m: float
    roof_shape: str
    roof_orientation: str
    building_class: BuildingClass = BuildingClass.UNKNOWN
    height_source: str = "fallback"
    building_type: str = "yes"
    levels: float | None = None
    footprint_area_m2: float = 0.0

    @property
    def eave_height_m(self) -> float:
        return max(self.min_height_m, self.total_height_m - self.roof_height_m)


def _number_m(value: object, *, allow_zero: bool = False) -> float | None:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    try:
        raw = str(value).strip().lower()
        number = (
            float(raw[:-2].strip()) * 0.3048
            if raw.endswith("ft")
            else float(raw.replace("m", "").strip())
        )
    except (TypeError, ValueError):
        return None
    if number < 0.0 or (number == 0.0 and not allow_zero):
        return None
    return number


def _building_dimensions(
    tags: dict, default_height_m: float, levels_to_m: float, max_height_m: float
) -> BuildingDimensions:
    """Interpret supported OSM Simple 3D Buildings tags in metres."""
    building_class = classify_building(tags)
    preset = preset_for(building_class)
    class_level_height_m = (
        preset.floor_height_m if building_class is not BuildingClass.UNKNOWN else levels_to_m
    )
    class_default_height_m = (
        preset.fallback_height_m
        if building_class is not BuildingClass.UNKNOWN
        else default_height_m
    )
    min_height = _number_m(tags.get("min_height"), allow_zero=True)
    if min_height is None:
        min_levels = _number_m(tags.get("building:min_level"), allow_zero=True)
        min_height = (min_levels or 0.0) * class_level_height_m
    roof_height = _number_m(tags.get("roof:height"), allow_zero=True)
    if roof_height is None:
        roof_levels = _number_m(tags.get("roof:levels"), allow_zero=True)
        roof_height = (roof_levels or 0.0) * class_level_height_m
    explicit_height = _number_m(tags.get("height"))
    levels = _number_m(tags.get("building:levels"))
    levels_height = levels * class_level_height_m + roof_height if levels is not None else None
    is_building_part = "building:part" in tags
    explicit_height_is_plausible = (
        explicit_height is not None
        and explicit_height <= max_height_m
        and (
            is_building_part
            or levels is None
            or explicit_height >= levels * MIN_PLAUSIBLE_LEVEL_HEIGHT_M
        )
    )
    height_source = "fallback"
    if explicit_height_is_plausible:
        total_height = explicit_height
        height_source = "height"
    elif levels_height is not None and levels_height <= max_height_m:
        total_height = levels_height
        height_source = "levels"
    elif building_class is not BuildingClass.UNKNOWN:
        total_height = min(class_default_height_m + roof_height, max_height_m)
        height_source = "class"
    else:
        total_height = _extract_real_height_m(tags, default_height_m, levels_to_m, max_height_m)
    total_height = min(max(total_height, min_height + 0.01), max_height_m)
    roof_height = min(roof_height, total_height - min_height)
    shape = str(tags.get("roof:shape", "flat")).strip().lower()
    if shape not in SUPPORTED_ROOF_SHAPES:
        shape = "flat"
    if shape == "flat":
        roof_height = 0.0
    if shape != "flat" and roof_height <= 0.0:
        roof_height = min(class_level_height_m, total_height - min_height)
    orientation = str(tags.get("roof:orientation", "along")).strip().lower()
    if orientation not in {"along", "across"}:
        orientation = "along"
    building_type_value = tags.get("building", "yes")
    if isinstance(building_type_value, (list, tuple)):
        building_type_value = building_type_value[0] if building_type_value else "yes"
    building_type = str(building_type_value).strip().lower()
    return BuildingDimensions(
        min_height,
        total_height,
        roof_height,
        shape,
        orientation,
        building_class,
        height_source,
        building_type,
        levels,
    )


def _roof_mesh(
    polygon: geom.Polygon,
    eave_z: float,
    roof_height_mm: float,
    shape: str,
    orientation: str = "along",
    base_overlap_mm: float = 0.0,
) -> Trimesh | None:
    """Create a faceted roof over an arbitrary footprint using oriented bounds."""
    if shape == "flat" or roof_height_mm <= 0.0:
        return None
    if base_overlap_mm < 0.0:
        raise ValueError("Roof overlap cannot be negative")
    corners = np.asarray(polygon.minimum_rotated_rectangle.exterior.coords[:4], dtype=float)
    edges = np.roll(corners, -1, axis=0) - corners
    lengths = np.linalg.norm(edges, axis=1)
    long_i = int(np.argmax(lengths))
    long_axis = edges[long_i] / max(lengths[long_i], 1e-9)
    short_axis = np.array([-long_axis[1], long_axis[0]])
    center_point = polygon.centroid
    if shape == "pyramidal" and not polygon.covers(center_point):
        # Concave crowns can have a centroid outside their footprint. In that
        # case an exterior centroid seed is discarded by constrained
        # triangulation and the roof silently loses its apex.
        center_point = polygon.representative_point()
    center = np.asarray(center_point.coords[0], dtype=float)
    projected = corners - center
    half_long = max(np.max(np.abs(projected @ long_axis)), 1e-9)
    half_short = max(np.max(np.abs(projected @ short_axis)), 1e-9)
    if orientation == "across":
        long_axis, short_axis = short_axis, long_axis
        half_long, half_short = half_short, half_long

    def z_at(x: float, y: float) -> float:
        delta = np.array([x, y]) - center
        u = float(np.clip((delta @ long_axis) / half_long, -1.0, 1.0))
        v = float(np.clip((delta @ short_axis) / half_short, -1.0, 1.0))
        if shape == "skillion":
            factor = (v + 1.0) / 2.0
        elif shape == "gabled":
            factor = 1.0 - abs(v)
        elif shape == "hipped":
            factor = min(1.0 - abs(v), (half_long / half_short) * (1.0 - abs(u)))
        else:
            factor = 1.0 - max(abs(u), abs(v))
        return eave_z + roof_height_mm * max(0.0, factor)

    seeds: list[geom.Point] = []
    if shape == "gabled":
        ridge = geom.LineString(
            [center - long_axis * half_long * 2.0, center + long_axis * half_long * 2.0]
        ).intersection(polygon)
        ridge_parts = list(ridge.geoms) if hasattr(ridge, "geoms") else [ridge]
        for part in ridge_parts:
            if not isinstance(part, geom.LineString) or part.is_empty:
                continue
            seeds.extend(geom.Point(coordinate) for coordinate in (part.coords[0], part.coords[-1]))
    elif shape == "hipped":
        ridge_half = max(0.0, half_long - half_short)
        seeds = [
            geom.Point(*(center - long_axis * ridge_half)),
            geom.Point(*(center + long_axis * ridge_half)),
        ]
    elif shape == "pyramidal":
        seeds = [geom.Point(*center)]
    triangulation_input = (
        geom.GeometryCollection([polygon, geom.MultiPoint(seeds)]) if seeds else polygon
    )
    xy_vertices: list[tuple[float, float]] = []
    vertex_indices: dict[tuple[float, float], int] = {}
    top_faces: list[list[int]] = []

    def vertex_index(x: float, y: float) -> int:
        key = (round(float(x), 12), round(float(y), 12))
        if key not in vertex_indices:
            vertex_indices[key] = len(xy_vertices)
            xy_vertices.append((float(x), float(y)))
        return vertex_indices[key]

    candidate_triangles: list[geom.Polygon] = []
    for triangle in triangulate(triangulation_input):
        if triangle.area <= 1e-10:
            continue
        clipped = triangle.intersection(polygon)
        clipped_parts = list(clipped.geoms) if hasattr(clipped, "geoms") else [clipped]
        for clipped_part in clipped_parts:
            if not isinstance(clipped_part, geom.Polygon) or clipped_part.area <= 1e-10:
                continue
            # Retriangulate clipped Delaunay faces so concave crowns retain
            # their roof seeds without creating faces outside the footprint.
            candidate_triangles.extend(
                subtriangle
                for subtriangle in triangulate(clipped_part)
                if subtriangle.area > 1e-10 and clipped_part.covers(subtriangle)
            )

    for triangle in candidate_triangles:
        coords = list(triangle.exterior.coords)[:3]
        signed_area = sum(
            coords[i][0] * coords[(i + 1) % 3][1] - coords[(i + 1) % 3][0] * coords[i][1]
            for i in range(3)
        )
        if signed_area < 0.0:
            coords.reverse()
        top_faces.append([vertex_index(x, y) for x, y in coords])
    if not top_faces:
        return None

    vertices = [[x, y, z_at(x, y)] for x, y in xy_vertices]
    bottom_z = eave_z - base_overlap_mm
    bottom_indices: list[int] = []
    for top_index, (x, y) in enumerate(xy_vertices):
        if base_overlap_mm <= 1e-9 and abs(vertices[top_index][2] - eave_z) <= 1e-9:
            bottom_indices.append(top_index)
        else:
            bottom_indices.append(len(vertices))
            vertices.append([x, y, bottom_z])
    faces = list(top_faces)
    faces.extend(
        [[bottom_indices[c], bottom_indices[b], bottom_indices[a]] for a, b, c in top_faces]
    )

    edge_counts: dict[tuple[int, int], int] = {}
    directed_edges: dict[tuple[int, int], tuple[int, int]] = {}
    for face in top_faces:
        for a, b in zip(face, face[1:] + face[:1]):
            key = (min(a, b), max(a, b))
            edge_counts[key] = edge_counts.get(key, 0) + 1
            directed_edges.setdefault(key, (a, b))
    for key, count in edge_counts.items():
        if count != 1:
            continue
        a, b = directed_edges[key]
        bottom_a = bottom_indices[a]
        bottom_b = bottom_indices[b]
        if a == bottom_a and b == bottom_b:
            continue
        if a == bottom_a:
            faces.append([a, bottom_b, b])
        elif b == bottom_b:
            faces.append([a, bottom_a, b])
        else:
            faces.extend([[a, bottom_a, bottom_b], [a, bottom_b, b]])

    return Trimesh(np.asarray(vertices), np.asarray(faces), process=True)


def _transform_shapely_polygon(
    polygon: geom.Polygon,
    center_lat: float,
    center_lon: float,
    transform: dict,
) -> geom.Polygon:
    """Apply lon/lat -> mm transform to all coordinates of a Shapely Polygon.

    Preserves all original vertices and ring order.
    """
    if polygon.is_empty:
        return polygon

    def _transform_ring(coords):
        coords = np.array(coords)
        if coords.size == 0:
            return coords
        lons = coords[:, 0]
        lats = coords[:, 1]
        projected = project_lonlat_array(lats, lons, center_lat=center_lat, center_lon=center_lon)
        frame = transform.get("map_frame")
        transformed = (
            frame.transform_projected(projected)
            if frame is not None
            else apply_transform(projected, transform)
        )
        return [(float(x), float(y)) for x, y in transformed]

    exterior = _transform_ring(polygon.exterior.coords)
    interiors = [_transform_ring(r.coords) for r in polygon.interiors]
    try:
        return geom.Polygon(exterior, interiors)
    except Exception:
        return polygon


def _extract_real_height_m(
    tags: dict,
    default_height_m: float,
    levels_to_m: float,
    max_height_m: float,
) -> float:
    """Extract real-world building height in metres from OSM tag dict.

    Priority:
    1. ``height`` tag (explicit metres; values tagged in feet are converted)
    2. ``building:levels`` × ``levels_to_m``
    3. ``default_height_m`` fallback

    Values above ``max_height_m`` are treated as corrupt and skipped.
    """
    height_val = tags.get("height")
    if height_val is not None:
        if isinstance(height_val, (list, tuple)):
            height_val = height_val[0] if height_val else None
        if height_val is not None:
            try:
                raw = str(height_val).strip()
                if raw.endswith("ft"):
                    # Convert feet to metres
                    h = float(raw[:-2].strip()) * 0.3048
                else:
                    h = float(raw.replace("m", "").strip())
                if 0.0 < h <= max_height_m:
                    return h
            except (ValueError, TypeError):
                pass

    levels_val = tags.get("building:levels")
    if levels_val is not None:
        if isinstance(levels_val, (list, tuple)):
            levels_val = levels_val[0] if levels_val else None
        if levels_val is not None:
            try:
                levels = float(str(levels_val).strip())
                max_levels = max_height_m / levels_to_m
                if 0.0 < levels <= max_levels:
                    return levels * levels_to_m
            except (ValueError, TypeError):
                pass

    roof_levels_val = tags.get("roof:levels")
    if roof_levels_val is not None:
        if isinstance(roof_levels_val, (list, tuple)):
            roof_levels_val = roof_levels_val[0] if roof_levels_val else None
        try:
            roof_levels = float(str(roof_levels_val).strip())
            estimated_height = default_height_m + roof_levels * levels_to_m
            if 0.0 < roof_levels and estimated_height <= max_height_m:
                return estimated_height
        except (ValueError, TypeError):
            pass

    building_type = tags.get("building")
    if isinstance(building_type, (list, tuple)):
        building_type = building_type[0] if building_type else None
    type_defaults = {
        "apartments": 12.0,
        "office": 12.0,
        "commercial": 9.0,
        "hotel": 12.0,
        "hospital": 12.0,
        "industrial": 8.0,
        "warehouse": 8.0,
        "retail": 6.0,
        "church": 12.0,
        "cathedral": 18.0,
        "garage": 3.0,
        "garages": 3.0,
        "shed": 3.0,
    }
    if building_type is not None:
        estimated = type_defaults.get(str(building_type).strip().lower())
        if estimated is not None:
            return min(estimated, max_height_m)

    return default_height_m


def _tags_from_gdf_row(row, columns: list[str]) -> dict:
    """Build a plain tag dict from a GeoDataFrame row, dropping missing values."""
    import math

    tags: dict = {}
    for col in columns:
        if col == "geometry":
            continue
        val = row[col]
        # Drop None and float NaN (covers pandas NaN without requiring pandas import)
        if val is None:
            continue
        try:
            if isinstance(val, float) and math.isnan(val):
                continue
        except (TypeError, ValueError):
            pass
        tags[col] = val

    # OSMnx stores stable element identity in its GeoDataFrame index rather
    # than ordinary tag columns. Preserve it so optional landmark recipes can
    # match data without relying on names or city-specific coordinates.
    row_name = getattr(row, "name", None)
    if isinstance(row_name, tuple) and len(row_name) >= 2:
        element_type = str(row_name[-2]).strip().lower()
        if element_type in {"node", "way", "relation"}:
            tags.setdefault("_osm_type", element_type)
            tags.setdefault("_osm_id", row_name[-1])
    return tags


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

OVERPASS_TIMEOUT = 20


def _configure_overpass(osmnx: object, endpoint: str) -> None:
    """Configure both legacy and current OSMnx Overpass settings."""
    settings = osmnx.settings
    if hasattr(settings, "overpass_url"):
        settings.overpass_url = endpoint
    if hasattr(settings, "overpass_endpoint"):
        settings.overpass_endpoint = endpoint
    if hasattr(settings, "requests_timeout"):
        settings.requests_timeout = OVERPASS_TIMEOUT


def _overpass_geometry(element: dict) -> geom.base.BaseGeometry | None:
    """Build polygon geometry from an Overpass way or multipolygon relation."""

    def ring(items: object) -> geom.Polygon | None:
        if not isinstance(items, list):
            return None
        coordinates = [
            (item["lon"], item["lat"])
            for item in items
            if isinstance(item, dict) and "lon" in item and "lat" in item
        ]
        if len(coordinates) < 3:
            return None
        try:
            polygon = geom.Polygon(coordinates)
            return polygon if not polygon.is_empty else None
        except Exception:
            return None

    if element.get("type") == "way":
        return ring(element.get("geometry"))
    if element.get("type") != "relation":
        return None
    outers: list[geom.Polygon] = []
    inners: list[geom.Polygon] = []
    for member in element.get("members", []):
        if not isinstance(member, dict) or member.get("type") != "way":
            continue
        polygon = ring(member.get("geometry"))
        if polygon is None:
            continue
        (inners if member.get("role") == "inner" else outers).append(polygon)
    if not outers:
        return None
    result = ops.unary_union(outers)
    if inners:
        result = result.difference(ops.unary_union(inners))
    return result


def _adaptive_height_mapper(
    real_heights_m: list[float],
    scale_mm_per_m: float,
    max_height_mm: float,
    min_height_mm: float,
) -> Callable[[float], float]:
    """Map real heights at print scale, compressing only the tallest outliers."""
    if not real_heights_m or scale_mm_per_m <= 0.0:
        return lambda height_m: max(min_height_mm, min(max_height_mm, height_m))
    maximum_real = max(real_heights_m)
    maximum_raw = maximum_real * scale_mm_per_m
    if maximum_raw <= max_height_mm:
        return lambda height_m: max(min_height_mm, min(max_height_mm, height_m * scale_mm_per_m))

    knee_real = float(np.percentile(real_heights_m, 95))
    if knee_real >= maximum_real:
        compression = max_height_mm / maximum_raw
        return lambda height_m: max(
            min_height_mm, min(max_height_mm, height_m * scale_mm_per_m * compression)
        )

    knee_raw = min(knee_real * scale_mm_per_m, max_height_mm * 0.8)
    lower_scale = knee_raw / knee_real if knee_real > 0.0 else scale_mm_per_m

    def mapped(height_m: float) -> float:
        if height_m <= knee_real:
            visible = height_m * lower_scale
        else:
            fraction = (height_m - knee_real) / (maximum_real - knee_real)
            visible = knee_raw + fraction * (max_height_mm - knee_raw)
        return max(min_height_mm, min(max_height_mm, visible))

    return mapped


_ACCESSORY_BUILDING_TYPES = frozenset({"carport", "garage", "garages", "kiosk", "shed"})
_SMALL_RESIDENTIAL_TYPES = frozenset(
    {"bungalow", "cabin", "detached", "house", "semidetached_house"}
)


def _tiered_building_height_mapper(
    dimensions: list[BuildingDimensions],
    max_height_mm: float,
    map_scale_mm_per_m: float | None = None,
    reference_scale_mm_per_m: float = 0.04,
) -> Callable[[BuildingDimensions, float, float], float]:
    """Create scale-aware architectural relief with readable low-rise massing.

    The lowest level uses 1.2/1.6/2.0 mm subtiers. Mid-rise buildings map
    continuously from 3–9 mm and high-rises from 9 mm to the configured ceiling.
    """
    if max_height_mm <= 0.0:
        raise ValueError("Maximum building height must be positive")
    if reference_scale_mm_per_m <= 0.0:
        raise ValueError("Building height reference scale must be positive")

    scale_factor = 1.0
    if map_scale_mm_per_m is not None and map_scale_mm_per_m > 0.0:
        scale_factor = float(
            np.clip(np.sqrt(map_scale_mm_per_m / reference_scale_mm_per_m), 0.45, 1.0)
        )
    scaled_ceiling = max(1.2, max_height_mm * scale_factor)

    def storey_signal(item: BuildingDimensions, real_height_m: float) -> float:
        if item.height_source == "levels" and item.levels is not None:
            return item.levels
        if item.height_source == "height":
            return real_height_m / 3.0
        if item.height_source == "class":
            return preset_for(item.building_class).fallback_levels
        return real_height_m / 3.0

    high_rises = sorted(
        storey_signal(item, item.total_height_m)
        for item in dimensions
        if item.height_source in {"height", "levels"}
        and storey_signal(item, item.total_height_m) >= 9.0
    )
    high_reference = float(np.percentile(high_rises, 99)) if high_rises else 9.0
    high_reference = max(high_reference, 9.01)

    def low_rise(item: BuildingDimensions, footprint_area_m2: float) -> float:
        if item.building_type in _ACCESSORY_BUILDING_TYPES:
            return 1.2
        if (
            item.building_class is BuildingClass.RESIDENTIAL
            and item.building_type in _SMALL_RESIDENTIAL_TYPES
            and footprint_area_m2 <= 180.0
        ):
            return 1.6
        if item.building_class is BuildingClass.UNKNOWN:
            if footprint_area_m2 <= 40.0:
                return 1.2
            if footprint_area_m2 <= 120.0:
                return 1.6
        return 2.0

    def mapped(
        item: BuildingDimensions,
        real_height_m: float,
        footprint_area_m2: float,
    ) -> float:
        low = max(1.2, low_rise(item, footprint_area_m2) * scale_factor)
        if item.building_type in _ACCESSORY_BUILDING_TYPES:
            return min(low, scaled_ceiling)
        storeys = storey_signal(item, real_height_m)
        if item.height_source == "levels" and item.levels is not None:
            if item.levels <= 1.0:
                return min(low, scaled_ceiling)
            if item.levels <= 2.0:
                return min(max(1.2, 2.8 * scale_factor), scaled_ceiling)
            if item.levels <= 8.0:
                fraction = (item.levels - 3.0) / 5.0
                return min(max(1.2, (3.0 + fraction * 6.0) * scale_factor), scaled_ceiling)
        confident_height = item.height_source in {"height", "levels"}
        inferred_tall_class = item.building_class in {
            BuildingClass.COMMERCIAL_OFFICE,
            BuildingClass.CIVIC_INSTITUTIONAL,
            BuildingClass.LANDMARK,
            BuildingClass.PARKING,
            BuildingClass.RELIGIOUS,
            BuildingClass.STADIUM_ARENA,
        }
        if storeys <= 2.0 or not (confident_height or inferred_tall_class):
            if confident_height and storeys > 1.0:
                fraction = min(1.0, storeys - 1.0)
                target = max(1.2, 2.8 * scale_factor)
                return min(low + fraction * (target - low), scaled_ceiling)
            return min(low, scaled_ceiling)
        if storeys < 3.0:
            return min(max(1.2, (2.8 + (storeys - 2.0) * 0.2) * scale_factor), scaled_ceiling)
        if storeys <= 8.0:
            fraction = (storeys - 3.0) / 5.0
            return min(max(1.2, (3.0 + fraction * 6.0) * scale_factor), scaled_ceiling)
        fraction = min(
            1.0,
            max(0.0, (storeys - 9.0) / (high_reference - 9.0)),
        )
        high_floor = max(1.2, 9.0 * scale_factor)
        high_ceiling = max(high_floor, scaled_ceiling)
        visible = high_floor + (fraction**0.72) * (high_ceiling - high_floor)
        return min(visible, scaled_ceiling)

    return mapped


def _stadium_recipe_for_landmark(
    landmark: LandmarkDefinition | None,
) -> StadiumRecipe | None:
    """Translate a versioned landmark recipe into the generic stadium builder."""
    if landmark is None or landmark.enhancement is None:
        return None
    enhancement = landmark.enhancement
    if enhancement.kind != "procedural_recipe":
        return None
    roof_styles = {
        "stadium_closed_roof": "closed",
        "stadium_retractable_roof": "retractable",
    }
    roof_style = roof_styles.get(enhancement.reference)
    if roof_style is None:
        logging.warning(
            "Unsupported procedural landmark recipe %s for %s",
            enhancement.reference,
            landmark.display_name,
        )
        return None
    metadata = enhancement.metadata
    return StadiumRecipe(
        roof_style=roof_style,
        bowl_height_mm=float(metadata.get("bowl_height_mm", 4.0)),
        tier_count=int(metadata.get("tier_count", 3)),
        minimum_feature_mm=float(metadata.get("minimum_feature_mm", 0.8)),
        roof_height_mm=float(metadata.get("roof_height_mm", 6.0)),
        roof_thickness_mm=float(metadata.get("roof_thickness_mm", 0.8)),
        roof_orientation_degrees=float(metadata.get("roof_orientation_degrees", 0.0)),
        roof_side=str(metadata.get("roof_side", "west")),
        roof_coverage=float(metadata.get("roof_coverage", 0.38)),
        roof_support_width_mm=float(metadata.get("roof_support_width_mm", 1.2)),
        support_overlap_mm=float(metadata.get("support_overlap_mm", 0.15)),
        closed_roof_band_count=int(metadata.get("closed_roof_band_count", 3)),
        closed_roof_band_width_mm=float(metadata.get("closed_roof_band_width_mm", 1.2)),
        closed_roof_band_height_mm=float(metadata.get("closed_roof_band_height_mm", 0.48)),
    )


def _supported_elevated_elements(
    elements: list[
        tuple[
            geom.base.BaseGeometry,
            BuildingDimensions,
            bool,
            LandmarkDefinition | None,
        ]
    ],
) -> set[int]:
    """Find elevated parts with a footprint and height support path to ground."""
    footprints = [element[0] for element in elements]
    tree = STRtree(footprints)
    supported = {
        index
        for index, (_poly, dimensions, _is_part, _landmark) in enumerate(elements)
        if dimensions.min_height_m <= 1e-9
    }
    pending = {
        index
        for index, (_poly, dimensions, _is_part, _landmark) in enumerate(elements)
        if dimensions.min_height_m > 1e-9
    }
    while pending:
        newly_supported: set[int] = set()
        for index in pending:
            footprint, dimensions, _is_part, _landmark = elements[index]
            for candidate in tree.query(footprint, predicate="intersects"):
                support_index = int(candidate)
                if support_index == index or support_index not in supported:
                    continue
                support_footprint, support_dimensions, _part, _landmark = elements[support_index]
                if support_dimensions.total_height_m < dimensions.min_height_m - 0.5:
                    continue
                if footprint.intersection(support_footprint).area <= 1e-8:
                    continue
                newly_supported.add(index)
                break
        if not newly_supported:
            break
        supported.update(newly_supported)
        pending.difference_update(newly_supported)
    return supported


def download_and_build_buildings(
    bbox: tuple[float, float, float, float] | None,
    center_lat: float,
    center_lon: float,
    transform: dict,
    map_width_mm: float,
    map_height_mm: float,
    margin_mm: float,
    debug: bool = False,
    z_offset: float = 0.0,
    embed_depth_mm: float = 0.0,
    max_print_height_mm: float = 30.0,
    min_building_height_mm: float = 1.2,
    building_default_height_m: float = 6.0,
    building_levels_to_m: float = 3.0,
    building_max_real_height_m: float = 400.0,
    building_clip_threshold: float = 0.5,
    radius_m: float | None = None,
    buildings_file: str | None = None,
    overlay_roads: object | None = None,
    route_points: np.ndarray | None = None,
    terrain_height_at: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None,
    building_scale_mm_per_m: float | None = None,
    extend_elevated_parts_to_ground: bool = True,
) -> tuple[geom.base.BaseGeometry | None, object | None]:
    """Download building footprints within bbox and return (unioned_polygons, mesh).

    Each building follows the physical horizontal map scale. Heights are adaptively
    compressed only when tall outliers exceed the configured maximum. Buildings
    where more than ``building_clip_threshold`` of their footprint lies outside the
    margin-inset build area are omitted; the remainder are clipped to that boundary.

    ``embed_depth_mm`` extends every building below the base top without reducing that
    visible height. All extruded buildings are concatenated into a single mesh.
    """
    # geo_with_tags: list of (shapely_geometry, tags_dict) collected from all sources
    geo_with_tags: list[tuple] = []

    if buildings_file is not None:
        try:
            import geopandas as gpd

            df = gpd.read_file(buildings_file)
            cols = list(df.columns)
            for _, row in df.iterrows():
                g = row.geometry
                if g is None:
                    continue
                geo_with_tags.append((g, _tags_from_gdf_row(row, cols)))
        except Exception as exc:
            logging.warning("Failed to read local buildings file %s: %s", buildings_file, exc)
            return None, None
    else:
        try:
            import osmnx as ox
        except Exception as exc:
            logging.warning("OSMnx not available or failed to import: %s", exc)
            return None, None

        bbox_for_query: tuple | None = None

        for endpoint in OVERPASS_ENDPOINTS:
            try:
                _configure_overpass(ox, endpoint)

                if radius_m is not None:
                    try:
                        features_from_point = getattr(ox, "features_from_point", None) or getattr(
                            ox, "geometries_from_point"
                        )
                        gdf = features_from_point(
                            (center_lat, center_lon),
                            tags={"building": True, "building:part": True},
                            dist=radius_m,
                        )
                        cols = list(gdf.columns)
                        for _, row in gdf.iterrows():
                            g = row.geometry
                            if g is None:
                                continue
                            geo_with_tags.append((g, _tags_from_gdf_row(row, cols)))
                        break
                    except Exception:
                        lat_delta = radius_m / 111000.0
                        lon_delta = radius_m / (
                            111000.0 * max(0.000001, np.cos(np.deg2rad(center_lat)))
                        )
                        bbox_for_query = (
                            center_lat - lat_delta,
                            center_lon - lon_delta,
                            center_lat + lat_delta,
                            center_lon + lon_delta,
                        )
                else:
                    lat_min, lat_max, lon_min, lon_max = bbox
                    try:
                        features_from_bbox = getattr(ox, "features_from_bbox", None) or getattr(
                            ox, "geometries_from_bbox"
                        )
                        if hasattr(ox, "features_from_bbox"):
                            gdf = features_from_bbox(
                                (lon_min, lat_min, lon_max, lat_max),
                                tags={"building": True, "building:part": True},
                            )
                        else:
                            gdf = features_from_bbox(
                                lat_max,
                                lat_min,
                                lon_max,
                                lon_min,
                                tags={"building": True, "building:part": True},
                            )
                        cols = list(gdf.columns)
                        for _, row in gdf.iterrows():
                            g = row.geometry
                            if g is None:
                                continue
                            geo_with_tags.append((g, _tags_from_gdf_row(row, cols)))
                        break
                    except Exception:
                        bbox_for_query = (lat_min, lon_min, lat_max, lon_max)

                # OSMnx failed — fall back to raw Overpass HTTP query
                if bbox_for_query is None:
                    geo_with_tags = []
                    continue

                try:
                    import requests

                    south, west, north, east = bbox_for_query
                    # Use "out body geom" so tags are included in the response
                    query = (
                        f"[out:json][timeout:18];\n"
                        f"(\n"
                        f'  way["building"]({south},{west},{north},{east});\n'
                        f'  way["building:part"]({south},{west},{north},{east});\n'
                        f'  relation["building"]({south},{west},{north},{east});\n'
                        f'  relation["building:part"]({south},{west},{north},{east});\n'
                        f");\n"
                        f"out body geom;\n"
                    )
                    headers = {
                        "User-Agent": "memorymap-pipeline/1.0 (+https://example.local)",
                        "Accept": "application/json",
                    }
                    resp = requests.post(
                        endpoint, data={"data": query}, headers=headers, timeout=OVERPASS_TIMEOUT
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    elements = data.get("elements", [])
                    for el in elements:
                        el_tags = dict(el.get("tags", {}))
                        if el.get("type") in {"node", "way", "relation"}:
                            el_tags["_osm_type"] = el["type"]
                        if el.get("id") is not None:
                            el_tags["_osm_id"] = el["id"]
                        polygon = _overpass_geometry(el)
                        if polygon is not None:
                            geo_with_tags.append((polygon, el_tags))
                    if geo_with_tags:
                        break
                except Exception as exc:
                    logging.warning("Overpass HTTP fetch failed via %s: %s", endpoint, exc)
                    geo_with_tags = []
                    continue

            except Exception as exc:
                logging.warning("Failed to download OSM building data via %s: %s", endpoint, exc)
                geo_with_tags = []
                continue

        if not geo_with_tags:
            return None, None

    try:
        landmark_registry = load_default_landmark_registry()
    except LandmarkRegistryError as exc:
        logging.warning("Landmark enhancements unavailable: %s", exc)
        landmark_registry = None

    # Margin-inset plate boundary used for clip/omit decisions
    plate_box = geom.box(margin_mm, margin_mm, map_width_mm - margin_mm, map_height_mm - margin_mm)
    frame = transform.get("map_frame")
    horizontal_scale_mm_per_m = (
        min(
            frame.printable_width_mm / frame.coverage_width_m,
            frame.printable_height_mm / frame.coverage_height_m,
        )
        if frame is not None
        else float(transform.get("scale", 1.0))
    )

    # Transform each polygon to mm coords, extract its height, clip, and filter
    elements: list[
        tuple[geom.base.BaseGeometry, BuildingDimensions, bool, LandmarkDefinition | None]
    ] = []

    for g, tags in geo_with_tags:
        if g is None:
            continue
        if g.geom_type == "Polygon":
            polys = [g]
        elif g.geom_type == "MultiPolygon":
            polys = list(g.geoms)
        else:
            continue

        landmark = None
        effective_tags = dict(tags)
        if landmark_registry is not None:
            try:
                landmark = landmark_registry.match_feature(
                    effective_tags,
                    osm_type=effective_tags.get("_osm_type"),
                    osm_id=effective_tags.get("_osm_id"),
                )
            except LandmarkRegistryError as exc:
                logging.warning("Skipping conflicting landmark identifiers: %s", exc)
            if landmark is not None:
                effective_tags.update(landmark.tag_corrections)

        dimensions = _building_dimensions(
            effective_tags,
            default_height_m=building_default_height_m,
            levels_to_m=building_levels_to_m,
            max_height_m=building_max_real_height_m,
        )

        for p in polys:
            if p.is_empty:
                continue

            try:
                tp = _transform_shapely_polygon(
                    p, center_lat=center_lat, center_lon=center_lon, transform=transform
                )
            except Exception:
                continue

            if not tp.is_valid:
                tp, _ok, _exp = repair_polygon(tp)
            if tp.is_empty:
                continue

            original_area = tp.area
            if original_area <= 0.0:
                continue
            element_dimensions = replace(
                dimensions,
                footprint_area_m2=(original_area / max(horizontal_scale_mm_per_m**2, 1e-12)),
            )

            # Clip to margin-inset plate boundary
            try:
                clipped = tp.intersection(plate_box)
            except Exception:
                continue

            if clipped.is_empty:
                continue

            fraction_inside = clipped.area / original_area
            # Omit buildings where the fraction of footprint inside the build area is
            # less than (1 - building_clip_threshold).  With a threshold of 0.5 this
            # discards any building that has more than 50 % of its area outside.
            if fraction_inside < (1.0 - building_clip_threshold):
                continue

            if not clipped.is_valid:
                clipped, _ok, _exp = repair_polygon(clipped)
            if clipped.is_empty:
                continue

            elements.append(
                (clipped, element_dimensions, "building:part" in effective_tags, landmark)
            )

    if not elements:
        return None, None

    part_union = ops.unary_union([poly for poly, _dims, is_part, _landmark in elements if is_part])
    if not part_union.is_empty:
        resolved: list[
            tuple[geom.base.BaseGeometry, BuildingDimensions, bool, LandmarkDefinition | None]
        ] = []
        for poly, dims, is_part, landmark in elements:
            remainder = poly if is_part else poly.difference(part_union)
            if not remainder.is_empty:
                resolved.append((remainder, dims, is_part, landmark))
        elements = resolved

    supported_elevated = _supported_elevated_elements(elements)

    # Architectural relief uses explicit low/mid/high visual tiers. Horizontal map
    # scale remains useful for recovering each footprint's real-world area.
    all_real_heights = [dims.total_height_m for _, dims, _, _ in elements]
    if building_scale_mm_per_m is None:
        frame = transform.get("map_frame")
        if frame is not None:
            building_scale_mm_per_m = min(
                frame.printable_width_mm / frame.coverage_width_m,
                frame.printable_height_mm / frame.coverage_height_m,
            )
        else:
            building_scale_mm_per_m = float(transform.get("scale", 1.0))
    tier_height = _tiered_building_height_mapper(
        [dims for _, dims, _, _ in elements],
        max_print_height_mm,
        map_scale_mm_per_m=building_scale_mm_per_m,
    )

    def real_area_m2(polygon: geom.base.BaseGeometry) -> float:
        return polygon.area / max(horizontal_scale_mm_per_m**2, 1e-12)

    def classified_height(
        dimensions: BuildingDimensions,
        real_height_m: float,
        footprint_area_m2: float,
    ) -> float:
        """Apply tiered massing while preserving roof and elevated-part proportions."""
        preset = preset_for(dimensions.building_class)
        total_visible = tier_height(dimensions, dimensions.total_height_m, footprint_area_m2)
        if dimensions.height_source not in {"height", "levels"}:
            total_visible = min(total_visible, preset.max_visual_height_mm)
        fraction = np.clip(real_height_m / max(dimensions.total_height_m, 1e-9), 0.0, 1.0)
        return float(total_visible * fraction)

    logging.info(
        "Buildings: %d footprints | real heights %.1f–%.1f m | "
        "map scale %.4f mm/m | extrusions %.2f–%.2f mm",
        len(elements),
        min(all_real_heights),
        max(all_real_heights),
        building_scale_mm_per_m,
        min(
            classified_height(dims, dims.total_height_m, dims.footprint_area_m2)
            for poly, dims, _, _ in elements
        ),
        max(
            classified_height(dims, dims.total_height_m, dims.footprint_area_m2)
            for poly, dims, _, _ in elements
        ),
    )

    # Extrude each building individually then concatenate into one mesh
    meshes = []
    surface_z = z_offset + embed_depth_mm
    for element_index, (poly, dimensions, _is_part, landmark) in enumerate(elements):
        parts: list[geom.Polygon] = list(poly.geoms) if poly.geom_type == "MultiPolygon" else [poly]
        parts.sort(key=lambda candidate: candidate.area, reverse=True)
        for part_index, part in enumerate(parts):
            if part.is_empty:
                continue
            try:
                footprint_area_m2 = dimensions.footprint_area_m2 or real_area_m2(part)
                terrain_z = 0.0
                if terrain_height_at is not None:
                    sample_coordinates = list(part.exterior.coords)
                    for interior in part.interiors:
                        sample_coordinates.extend(interior.coords)
                    sample_coordinates.extend(
                        (part.centroid.coords[0], part.representative_point().coords[0])
                    )
                    samples = np.asarray(sample_coordinates, dtype=float)
                    terrain_z = float(np.min(terrain_height_at(samples[:, 0], samples[:, 1])))

                landmark_recipe = _stadium_recipe_for_landmark(landmark)
                if part_index == 0 and dimensions.building_class is BuildingClass.STADIUM_ARENA:
                    try:
                        stadium = build_stadium_mesh(
                            part,
                            recipe=landmark_recipe or StadiumRecipe(),
                        )
                        stadium.apply_translation(
                            (0.0, 0.0, surface_z + terrain_z - embed_depth_mm)
                        )
                        meshes.append(stadium)
                        logging.info(
                            "Applied %s stadium geometry to %s",
                            "landmark" if landmark_recipe is not None else "generic",
                            landmark.display_name if landmark is not None else "OSM stadium",
                        )
                        continue
                    except Exception as exc:
                        logging.warning(
                            "Stadium enhancement failed; using standard building mass: %s",
                            exc,
                        )
                bottom_mm = (
                    classified_height(
                        dimensions,
                        dimensions.min_height_m,
                        footprint_area_m2,
                    )
                    if dimensions.min_height_m > 0.0
                    and (not extend_elevated_parts_to_ground or element_index in supported_elevated)
                    else 0.0
                )
                total_mm = classified_height(
                    dimensions,
                    dimensions.total_height_m,
                    footprint_area_m2,
                )
                minimum_body_mm = min(0.6, total_mm)
                bottom_mm = min(bottom_mm, max(0.0, total_mm - minimum_body_mm))
                eave_mm = min(
                    total_mm,
                    max(
                        bottom_mm + minimum_body_mm,
                        classified_height(dimensions, dimensions.eave_height_m, footprint_area_m2),
                    ),
                )
                effective_embed = (
                    embed_depth_mm if bottom_mm <= 1e-9 else min(embed_depth_mm, bottom_mm)
                )
                meshes.append(
                    route_mesh_from_polygon(
                        part,
                        height_mm=(eave_mm - bottom_mm) + effective_embed,
                        z_offset=surface_z + terrain_z + bottom_mm - effective_embed,
                    )
                )
                roof = _roof_mesh(
                    part,
                    eave_z=surface_z + terrain_z + eave_mm,
                    roof_height_mm=max(
                        0.0,
                        total_mm - eave_mm,
                    ),
                    shape=dimensions.roof_shape,
                    orientation=dimensions.roof_orientation,
                    base_overlap_mm=embed_depth_mm,
                )
                if roof is not None:
                    meshes.append(roof)
            except Exception as exc:
                logging.warning("Failed creating building mesh: %s", exc)

    final_mesh = None
    if meshes:
        try:
            from trimesh.util import concatenate

            final_mesh = concatenate(meshes)
        except Exception:
            final_mesh = meshes[0]

    # Union of all clipped footprints (used for debug overlay and return value)
    unioned = ops.unary_union([p for p, _dims, _is_part, _landmark in elements])

    if debug:
        try:
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(6, 8))
            for poly, _dimensions, _is_part, _landmark in elements:
                try:
                    parts = list(poly.geoms) if poly.geom_type == "MultiPolygon" else [poly]
                    for part in parts:
                        x, y = part.exterior.xy
                        ax.fill(x, y, alpha=0.6, fc="cyan", ec="black")
                except Exception:
                    pass
            if overlay_roads is not None:
                try:
                    road_parts = (
                        list(overlay_roads.geoms)
                        if overlay_roads.geom_type in ("MultiPolygon", "GeometryCollection")
                        else [overlay_roads]
                    )
                    for r in road_parts:
                        if r.is_empty or not hasattr(r, "exterior"):
                            continue
                        x, y = r.exterior.xy
                        ax.plot(x, y, color="black", linewidth=0.5)
                except Exception:
                    pass
            if route_points is not None:
                try:
                    ax.plot(route_points[:, 0], route_points[:, 1], color="red", linewidth=1.0)
                except Exception:
                    pass
            ax.set_aspect("equal", adjustable="box")
            fig.savefig("buildings_debug.png", dpi=150)
            plt.close(fig)
        except Exception:
            pass

    return unioned, final_mesh
