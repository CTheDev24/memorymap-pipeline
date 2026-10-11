from __future__ import annotations

import json
import logging
import math
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

import numpy as np
from shapely import area, contains_xy, intersection, intersects, polygons, prepare
from shapely.geometry import LineString, MultiPoint, box
from shapely.ops import unary_union
from trimesh.util import concatenate

from .build_identity import build_identity
from .building_heights import BuildingHeightPolicy
from .buildings import download_and_build_buildings
from .city_relief import MAJOR_ROADS
from .config import DEFAULT_CONFIG, route_height_for_profile, route_slope_for_layer_height
from .geometry import buffered_polygon_from_points, repair_polygon
from .gpx_loader import Route
from .impact_observatory import ImpactObservatoryProvider
from .landcover import (
    LandCoverClass,
    LandCoverGrid,
    LandCoverRequest,
    cleanup_exposed_mask,
    fuse_coastal_evidence,
    polygonize_exposed_mask,
    rasterize_geometry_mask,
)
from .map_frame import MapFrame
from .mesh import (
    build_base_plate,
    embedded_feature_dimensions,
    export_3mf,
    refine_mesh_edges,
    route_mesh_from_polygon,
)
from .print_scale import PrintScaleContext
from .roads import download_and_build_roads, resolve_road_style
from .route_markers import RouteMarkerMode, build_route_marker, marker_centers
from .surface_layers import (
    build_conformal_surface_skin,
    download_coastal_exposure_evidence,
    download_exposed_land_polygons,
    landscape_surface_region,
    recess_terrain_surface,
)
from .terrain import (
    ElevationGrid,
    build_terrain_mesh,
    drape_mesh,
    drape_road_mesh,
    drape_route_mesh,
    elevation_grid_for_frame,
    terrain_grid_for_print,
    terrain_mesh_heights,
    terrain_surface_from_grid,
)
from .terrain_providers import TerrariumProvider, Usgs3depProvider
from .water import (
    WaterFeature,
    build_printable_vector_water_mesh,
    build_terrain_mesh_with_water,
    download_water_polygons,
    prepare_water_bodies,
)
from .worldcover import WorldCoverProvider

ProgressCallback = Callable[[int, str], None]


class LayerGenerationError(RuntimeError):
    """Retryable failure with an explicit layer state; no successful audit is emitted."""

    def __init__(self, layer, message):
        super().__init__(message)
        self.layer_states = {layer: "failed"}


class _WarningCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


def _configure_packaged_networking() -> None:
    """Use writable cache and explicit CA paths in source and frozen builds."""
    try:
        import certifi

        certificate_path = certifi.where()
        os.environ.setdefault("SSL_CERT_FILE", certificate_path)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certificate_path)
    except ImportError:
        pass
    try:
        import osmnx as ox

        local_data = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MemoryMap"
        cache_dir = local_data / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        ox.settings.cache_folder = cache_dir
        ox.settings.use_cache = True
    except (ImportError, OSError):
        pass


@dataclass
class GenerationRequest:
    route: Route
    frame: MapFrame
    output_path: str | Path
    include_base: bool = True
    include_route: bool = True
    include_roads: bool = True
    include_buildings: bool = True
    route_width_mm: float = 1.2
    route_height_mm: float | None = None
    base_thickness_mm: float = 1.6
    config: dict[str, Any] = field(default_factory=dict)
    roads_file: str | Path | None = None
    buildings_file: str | Path | None = None
    elevation_grid: ElevationGrid | None = None
    water_polygons: list[Any] | None = None
    water_file: str | Path | None = None
    landcover_file: str | Path | None = None
    landcover_grid: LandCoverGrid | None = None
    building_diagnostics: list[dict] | None = None
    open_space_polygons: list[Any] | None = None
    # Internal analysis may inspect meshes before the normal audited export.
    export_model: bool = True
    map_style: str | None = None


@dataclass
class GenerationResult:
    output_path: Path
    warnings: list[str]
    stats: dict[str, Any]
    base_mesh: Any | None = None
    route_mesh: Any | None = None
    roads_mesh: Any | None = None
    buildings_mesh: Any | None = None
    water_mesh: Any | None = None
    landscape_mesh: Any | None = None
    start_marker_mesh: Any | None = None
    finish_marker_mesh: Any | None = None
    road_class_meshes: dict[str, Any] = field(default_factory=dict)

    @property
    def meshes(self) -> dict[str, Any]:
        layers = {
            name: mesh
            for name, mesh in {
                "base": self.base_mesh,
                "route": self.route_mesh,
                "roads": self.roads_mesh,
                "buildings": self.buildings_mesh,
                "water": self.water_mesh,
                "landscape": self.landscape_mesh,
                "start_marker": self.start_marker_mesh,
                "finish_marker": self.finish_marker_mesh,
            }.items()
            if mesh is not None
        }
        if self.road_class_meshes:
            layers.pop("roads", None)
            layers.update({f"roads_{key}": mesh for key, mesh in self.road_class_meshes.items()})
        return layers


def _merged_config(overrides: dict[str, Any]) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


def _query_bounds(frame: MapFrame) -> tuple[tuple[float, float, float, float], float]:
    radius = math.hypot(frame.coverage_width_m, frame.coverage_height_m) / 2.0
    margin = frame.margin_mm
    x = np.asarray(
        [margin, frame.print_width_mm - margin] * 2,
        dtype=float,
    )
    y = np.asarray(
        [
            margin,
            margin,
            frame.print_height_mm - margin,
            frame.print_height_mm - margin,
        ],
        dtype=float,
    )
    latitudes, longitudes = frame.print_to_lonlat(x, y)
    return (
        float(np.min(latitudes)),
        float(np.max(latitudes)),
        float(np.min(longitudes)),
        float(np.max(longitudes)),
    ), radius


def _frame_for_border(
    frame: MapFrame,
    flat_border_enabled: bool,
) -> MapFrame:
    """Use the selected frame margin only when a physical trim is requested."""
    if flat_border_enabled or frame.margin_mm == 0.0:
        return frame
    return replace(frame, margin_mm=0.0)


def _route_mesh(polygon: Any, height_mm: float, z_offset: float) -> Any | None:
    if polygon.is_empty:
        return None
    parts = list(polygon.geoms) if polygon.geom_type == "MultiPolygon" else [polygon]
    meshes = []
    for part in parts:
        if part.is_empty:
            continue
        try:
            meshes.append(route_mesh_from_polygon(part, height_mm, z_offset))
        except Exception as exc:
            logging.warning("Skipping invalid route polygon during extrusion: %s", exc)
    if not meshes:
        return None
    return meshes[0] if len(meshes) == 1 else concatenate(meshes)


def _feature_support_sampler(
    terrain_surface: Any,
    water_bodies: list[Any],
    flat_margin_mm: float = 0.0,
) -> Callable[[Any, Any], np.ndarray]:
    """Sample the actual printable surface, including recessed water tops."""
    buffered_water = [(body.geometry.buffer(1e-7), float(body.level_mm)) for body in water_bodies]

    def sample(x_mm, y_mm):
        x = np.asarray(x_mm, dtype=float)
        y = np.asarray(y_mm, dtype=float)
        heights = np.asarray(
            terrain_mesh_heights(
                terrain_surface,
                x,
                y,
                flat_margin_mm,
            ),
            dtype=float,
        ).copy()
        for geometry, level in buffered_water:
            heights = np.where(contains_xy(geometry, x, y), level, heights)
        return heights

    return sample


def _mesh_stats(mesh: Any | None) -> dict[str, int] | None:
    if mesh is None:
        return None
    return {"vertices": int(len(mesh.vertices)), "faces": int(len(mesh.faces))}


def _mesh_edge_defects(mesh: Any) -> tuple[int, int]:
    """Return open and over-connected edge counts for an assembled mesh."""
    edge_counts = np.bincount(mesh.edges_unique_inverse)
    return (
        int(np.count_nonzero(edge_counts == 1)),
        int(np.count_nonzero(edge_counts > 2)),
    )


def _continuous_water_support_surface(
    surface: Any,
    water_bodies: list[Any],
    water_mesh_thickness_mm: float,
    support_overlap_mm: float,
) -> Any:
    """Flatten a continuous height field beneath vector water bodies.

    This is the topology-safe fallback for a coastal partition that cannot be
    welded manifoldly. It preserves the intended water/body overlap without
    allowing DEM terrain (especially interpolated offshore values) to punch
    through a flat ocean surface.
    """
    heights = np.asarray(surface.heights_mm, dtype=float).copy()
    rows, columns = heights.shape
    xs = np.linspace(0.0, surface.width_mm, columns)
    ys = np.linspace(surface.height_mm, 0.0, rows)
    x_grid, y_grid = np.meshgrid(xs, ys)
    for body in water_bodies:
        support_level = float(body.level_mm) - water_mesh_thickness_mm + support_overlap_mm
        region = body.geometry.buffer(1e-7)
        heights[contains_xy(region, x_grid, y_grid)] = support_level
    return replace(surface, heights_mm=heights)


def _restore_hydroflattened_land(
    surface: Any,
    water_geometries: list[Any],
) -> tuple[Any, int]:
    """Inpaint an exact DEM water plateau where it falls outside mapped water."""
    valid_water = [geometry for geometry in water_geometries if not geometry.is_empty]
    if not valid_water:
        return surface, 0
    water_region = unary_union(valid_water).buffer(1e-7)
    heights = np.asarray(surface.heights_mm, dtype=float)
    rows, columns = heights.shape
    xs = np.linspace(0.0, surface.width_mm, columns)
    ys = np.linspace(surface.height_mm, 0.0, rows)
    x_grid, y_grid = np.meshgrid(xs, ys)
    water_mask = np.asarray(contains_xy(water_region, x_grid, y_grid), dtype=bool)
    if np.count_nonzero(water_mask) < max(16, heights.size // 200):
        return surface, 0

    rounded = np.round(heights[water_mask], decimals=9)
    levels, counts = np.unique(rounded, return_counts=True)
    plateau_level = float(levels[int(np.argmax(counts))])
    if int(np.max(counts)) < max(16, len(rounded) // 20):
        return surface, 0
    repair = np.isclose(heights, plateau_level, atol=5e-9, rtol=0.0) & ~water_mask
    repair_count = int(np.count_nonzero(repair))
    if repair_count < max(16, heights.size // 2000):
        return surface, 0

    repaired = heights.copy()
    known = ~repair & ~water_mask
    remaining = repair.copy()
    for _ in range(rows + columns):
        if not np.any(remaining):
            break
        totals = np.zeros_like(repaired)
        neighbour_counts = np.zeros_like(repaired, dtype=np.int16)
        for row_offset, column_offset in ((-1, 0), (0, -1), (0, 1), (1, 0)):
            source_rows = slice(max(0, -row_offset), rows - max(0, row_offset))
            source_columns = slice(max(0, -column_offset), columns - max(0, column_offset))
            target_rows = slice(max(0, row_offset), rows - max(0, -row_offset))
            target_columns = slice(max(0, column_offset), columns - max(0, -column_offset))
            neighbour_known = known[source_rows, source_columns]
            totals[target_rows, target_columns] += np.where(
                neighbour_known, repaired[source_rows, source_columns], 0.0
            )
            neighbour_counts[target_rows, target_columns] += neighbour_known
        fillable = remaining & (neighbour_counts > 0)
        if not np.any(fillable):
            break
        repaired[fillable] = totals[fillable] / neighbour_counts[fillable]
        known[fillable] = True
        remaining[fillable] = False

    filled_count = repair_count - int(np.count_nonzero(remaining))
    if filled_count == 0:
        return surface, 0
    return replace(surface, heights_mm=repaired), filled_count


def _building_route_overlap(triangles, route_exclusion, progress_callback=None, *, chunk_size=4096):
    """Check every projected mesh face, including roofs, with exact intersections.

    Prepared predicates reject disjoint faces cheaply. Chunking bounds temporary
    polygon memory and provides progress without weakening the clearance gate.
    """
    if route_exclusion.is_empty:
        if progress_callback is not None:
            progress_callback(1.0)
        return 0.0
    prepare(route_exclusion)
    overlaps = []
    for start in range(0, len(triangles), chunk_size):
        projected = polygons(triangles[start : start + chunk_size])
        projected = projected[area(projected) > 1e-12]
        hits = projected[intersects(route_exclusion, projected)]
        overlaps.append(area(intersection(hits, route_exclusion)))
        if progress_callback is not None:
            progress_callback(min(1.0, (start + chunk_size) / len(triangles)))
    if progress_callback is not None:
        progress_callback(1.0)
    return float(np.sum(np.concatenate(overlaps))) if overlaps else 0.0


def generate_memory_map(
    request: GenerationRequest,
    progress_callback: ProgressCallback | None = None,
) -> GenerationResult:
    from .map_styles import resolve_request

    request = resolve_request(request)
    def progress(value: int, message: str) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(value, message)
        except TypeError:
            progress_callback(value)  # type: ignore[misc,call-arg]

    if len(request.route.points) < 2:
        raise ValueError("At least two route points are required")
    if min(request.route_width_mm, request.base_thickness_mm) <= 0:
        raise ValueError("Mesh dimensions must be positive")

    progress(0, "Preparing print frame")
    _configure_packaged_networking()
    from .city_relief import configure

    config = _merged_config(request.config)
    original_road_height = config["road_height"]
    original_road_types = config["road_types"]
    config = configure(config)
    if request.map_style in {"cityscape", "street_grid"}:
        config.update(road_height=original_road_height, road_types=original_road_types)
    height_policy = BuildingHeightPolicy.from_config(config)
    flat_border_enabled = bool(config.get("flat_border_enabled", False))
    frame = _frame_for_border(request.frame, flat_border_enabled)
    print_scale = PrintScaleContext.from_frame(frame, config)
    print_optimized = config["building_generalization_mode"] == "print_optimized"
    if print_optimized:
        config.update(
            building_grouping_enabled=True,
            residential_min_width_mm=0.0,
            building_grouping_width_mm=print_scale.robust_width_mm,
            building_grouping_gap_mm=print_scale.merge_gap_mm,
            building_grouping_span_mm=print_scale.group_span_mm,
        )
    building_records = request.building_diagnostics
    if (print_optimized or height_policy.active) and building_records is None:
        building_records = []
    style_profile = str(config.get("style_profile", "urban"))
    if style_profile not in {"urban", "landscape"}:
        raise ValueError(f"Unsupported style profile: {style_profile}")
    landscape_style = style_profile == "landscape"
    route_height_mm = route_height_for_profile(style_profile, request.route_height_mm)
    surface_skin_thickness_mm = float(config.get("surface_skin_thickness_mm", 0.4))
    water_mesh_thickness_mm = float(config.get("water_mesh_thickness_mm", 0.6))
    water_support_overlap_mm = float(config.get("water_support_overlap_mm", 0.4))
    if landscape_style:
        landscape_water_visible_mm = float(config.get("landscape_water_visible_thickness_mm", 0.4))
        if not 0 < landscape_water_visible_mm <= water_mesh_thickness_mm:
            raise ValueError(
                "Landscape water visible thickness must be positive and no greater "
                "than the water mesh thickness"
            )
        water_support_overlap_mm = water_mesh_thickness_mm - landscape_water_visible_mm
    output_path = Path(request.output_path)
    scaled = frame.transform_points(request.route.points)
    printable = box(
        frame.margin_mm,
        frame.margin_mm,
        frame.print_width_mm - frame.margin_mm,
        frame.print_height_mm - frame.margin_mm,
    )
    route_extrusion_mm, z_offset, feature_embed_mm = embedded_feature_dimensions(
        route_height_mm,
        request.base_thickness_mm if request.include_base else 0.0,
        float(config.get("feature_embed_depth", 0.2)),
    )
    warnings: list[str] = []
    bbox, radius = _query_bounds(frame)
    transform = {"map_frame": frame}
    terrain_surface = None
    terrain_grid_spec = None
    water_mesh = None
    landscape_mesh = None
    water_bodies = []
    water_geometries: list[Any] = []
    linear_water_geometries: list[Any] = []
    linear_water_region = None
    ground_cover_stats = None
    flat_water_context = (request.map_style in {"cityscape", "street_grid"}
                          and bool(config.get("water_enabled", False)))
    if request.include_base and (bool(config.get("terrain_enabled", False)) or flat_water_context):
        terrain_target_cell_size_mm = float(
            config.get(
                "landscape_terrain_target_cell_size_mm"
                if landscape_style
                else "terrain_target_cell_size_mm",
                0.4 if landscape_style else 0.55,
            )
        )
        terrain_grid_max_samples = int(
            config.get(
                "landscape_terrain_grid_max_samples"
                if landscape_style
                else "terrain_grid_max_samples",
                240_000 if landscape_style else 180_000,
            )
        )
        terrain_grid_max_dimension = int(
            config.get(
                "landscape_terrain_grid_max_dimension"
                if landscape_style
                else "terrain_grid_max_dimension",
                640 if landscape_style else 512,
            )
        )
        terrain_grid_spec = terrain_grid_for_print(
            frame.print_width_mm,
            frame.print_height_mm,
            override=config.get("terrain_grid_size"),
            target_cell_size_mm=terrain_target_cell_size_mm,
            maximum_samples=terrain_grid_max_samples,
            maximum_dimension=terrain_grid_max_dimension,
        )
        grid_size = terrain_grid_spec.shape
        elevation_grid = request.elevation_grid
        if flat_water_context:
            south, north, west, east = bbox
            elevation_grid = ElevationGrid(np.zeros((2, 2)), south, north, west, east,
                                          "flat-water-support")
        if elevation_grid is None:
            if config.get("terrain_provider", "usgs-3dep") != "usgs-3dep":
                raise ValueError(f"Unsupported terrain provider: {config.get('terrain_provider')}")
            cache_dir = (
                Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MemoryMap" / "dem-cache"
            )
            provider = Usgs3depProvider(
                timeout_seconds=float(config.get("terrain_request_timeout_seconds", 20.0)),
                max_attempts=int(config.get("terrain_request_attempts", 3)),
                backoff_seconds=float(config.get("terrain_retry_backoff_seconds", 0.5)),
            )
            try:
                elevation_grid = provider.fetch(bbox, grid_size, cache_dir)
            except Exception as usgs_exc:
                warnings.append(
                    f"USGS terrain unavailable; trying the global DEM fallback: {usgs_exc}"
                )
                global_provider = TerrariumProvider(
                    timeout_seconds=float(config.get("terrain_fallback_timeout_seconds", 15.0)),
                    max_attempts=int(config.get("terrain_fallback_attempts", 2)),
                    backoff_seconds=float(config.get("terrain_retry_backoff_seconds", 0.5)),
                    zoom=int(config.get("terrain_fallback_zoom", 12)),
                )
                try:
                    elevation_grid = global_provider.fetch(bbox, grid_size, cache_dir)
                except Exception as fallback_exc:
                    if not bool(config.get("terrain_flat_fallback", True)):
                        raise LayerGenerationError("terrain",
                            f"USGS terrain failed ({usgs_exc}); "
                            f"global DEM fallback failed ({fallback_exc})"
                        ) from fallback_exc
                    warnings.append(
                        "Global terrain fallback unavailable; using a flat base for "
                        f"this generation: {fallback_exc}"
                    )
                    south, north, west, east = bbox
                    elevation_grid = ElevationGrid(
                        np.zeros(grid_size, dtype=float),
                        south,
                        north,
                        west,
                        east,
                        "flat-fallback",
                    )
        else:
            provided_rows, provided_columns = elevation_grid.elevations_m.shape
            terrain_grid_spec = terrain_grid_for_print(
                frame.print_width_mm,
                frame.print_height_mm,
                override=(provided_rows, provided_columns),
            )
        elevation_grid = elevation_grid_for_frame(
            elevation_grid,
            frame,
            terrain_grid_spec.shape,
        )
        maximum_relief_mm = float(config.get("terrain_max_relief_mm", 3.0))
        minimum_relief_mm = float(config.get("terrain_min_relief_mm", 1.5))
        if landscape_style:
            minimum_relief_mm = max(
                minimum_relief_mm,
                maximum_relief_mm * float(config.get("landscape_minimum_relief_ratio", 0.75)),
            )
        terrain_surface = terrain_surface_from_grid(
            elevation_grid,
            frame.print_width_mm,
            frame.print_height_mm,
            math.hypot(frame.coverage_width_m, frame.coverage_height_m),
            maximum_relief_mm,
            minimum_relief_mm,
            float(config.get("terrain_detail_gamma", 0.75)),
        )
        if bool(config.get("water_enabled", False)):
            water_polygons = request.water_polygons
            if water_polygons is None:
                downloaded_water = download_water_polygons(
                    bbox=bbox,
                    center_lat=frame.center_lat,
                    center_lon=frame.center_lon,
                    transform=transform,
                    map_width_mm=frame.print_width_mm,
                    map_height_mm=frame.print_height_mm,
                    radius_m=radius,
                    water_file=request.water_file,
                    minimum_waterway_width_mm=float(config.get("minimum_waterway_width_mm", 0.8)),
                    include_metadata=landscape_style,
                    strict=bool(config.get("building_grouping_enabled", False)) or request.map_style is not None,
                )
                if landscape_style:
                    water_features = [
                        feature for feature in downloaded_water if isinstance(feature, WaterFeature)
                    ]
                    water_geometries = [feature.geometry for feature in water_features]
                    linear_water_geometries = [
                        feature.geometry for feature in water_features if feature.kind == "waterway"
                    ]
                    water_polygons = [
                        feature.geometry for feature in water_features if feature.kind != "waterway"
                    ]
                else:
                    water_polygons = list(downloaded_water)
                    water_geometries = list(water_polygons)
            else:
                water_polygons = list(water_polygons)
                water_geometries = list(water_polygons)
            if landscape_style and water_geometries:
                terrain_surface, restored_land_samples = _restore_hydroflattened_land(
                    terrain_surface, water_geometries
                )
                if restored_land_samples:
                    warnings.append(
                        "Restored terrain relief for "
                        f"{restored_land_samples:,} hydro-flattened coastal land samples."
                    )
            if water_polygons:
                water_bodies = prepare_water_bodies(
                    water_polygons,
                    terrain_surface,
                    float(config.get("water_recess_mm", 0.4)),
                    surface_offset_mm=(surface_skin_thickness_mm if landscape_style else 0.0),
                    minimum_height_mm=-request.base_thickness_mm
                    + float(config.get("water_base_skin_mm", 0.4))
                    + water_mesh_thickness_mm,
                    shoreline_tolerance_mm=float(config.get("water_shoreline_tolerance_mm", 0.1)),
                    margin_mm=frame.margin_mm,
                )
                water_mesh, water_bodies = build_printable_vector_water_mesh(
                    water_bodies,
                    water_mesh_thickness_mm,
                )
            if linear_water_geometries:
                linear_water_region = (
                    unary_union(linear_water_geometries).buffer(0).intersection(printable)
                )
                if water_bodies:
                    linear_water_region = linear_water_region.difference(
                        unary_union([body.geometry for body in water_bodies])
                    ).buffer(0)
                linear_visible_mm = (
                    landscape_water_visible_mm if landscape_style else surface_skin_thickness_mm
                )
                linear_surface_offset_mm = (
                    surface_skin_thickness_mm
                    - float(config.get("water_recess_mm", 0.4))
                    - linear_visible_mm
                )
                linear_water_mesh = build_conformal_surface_skin(
                    terrain_surface,
                    linear_water_region,
                    visible_thickness_mm=linear_visible_mm,
                    embed_depth_mm=water_mesh_thickness_mm - linear_visible_mm,
                    surface_offset_mm=linear_surface_offset_mm,
                    clip_region=printable,
                    flat_margin_mm=frame.margin_mm,
                )
                if linear_water_mesh is not None:
                    water_mesh = (
                        linear_water_mesh
                        if water_mesh is None
                        else concatenate((water_mesh, linear_water_mesh))
                    )
            if not water_geometries:
                warnings.append("Water is enabled but no water polygons were supplied.")
        base_terrain_surface = terrain_surface
        if linear_water_region is not None and not linear_water_region.is_empty:
            conformal_support_offset_mm = (
                surface_skin_thickness_mm
                - float(config.get("water_recess_mm", 0.4))
                - (landscape_water_visible_mm if landscape_style else surface_skin_thickness_mm)
            )
            base_terrain_surface = recess_terrain_surface(
                terrain_surface,
                linear_water_region,
                max(0.0, -conformal_support_offset_mm),
            )
        if water_bodies:
            base_mesh = build_terrain_mesh_with_water(
                base_terrain_surface,
                request.base_thickness_mm,
                water_bodies,
                water_mesh_thickness_mm,
                water_support_overlap_mm,
                flat_margin_mm=frame.margin_mm,
            )
            open_edges, overconnected_edges = _mesh_edge_defects(base_mesh)
            if open_edges or overconnected_edges:
                logging.info(
                    "Detailed water recess produced a non-manifold structural base "
                    "(%d open and %d over-connected edges); using continuous terrain "
                    "support instead.",
                    open_edges,
                    overconnected_edges,
                )
                continuous_support_surface = _continuous_water_support_surface(
                    base_terrain_surface,
                    water_bodies,
                    water_mesh_thickness_mm,
                    water_support_overlap_mm,
                )
                base_mesh = build_terrain_mesh(
                    continuous_support_surface,
                    request.base_thickness_mm,
                    flat_margin_mm=frame.margin_mm,
                )
        else:
            base_mesh = build_terrain_mesh(
                base_terrain_surface,
                request.base_thickness_mm,
                flat_margin_mm=frame.margin_mm,
            )
        if landscape_style:
            ground_cover_mode = str(config.get("ground_cover_mode", "auto"))
            if ground_cover_mode not in {"auto", "osm-only", "off"}:
                raise ValueError("Ground-cover mode must be 'auto', 'osm-only', or 'off'")
            exposed_land = (
                download_exposed_land_polygons(
                    bbox=bbox,
                    center_lat=frame.center_lat,
                    center_lon=frame.center_lon,
                    transform=transform,
                    map_width_mm=frame.print_width_mm,
                    map_height_mm=frame.print_height_mm,
                    radius_m=radius,
                    landcover_file=request.landcover_file,
                )
                if bool(config.get("exposed_land_enabled", True)) and ground_cover_mode != "off"
                else []
            )
            osm_exposed_land = list(exposed_land)
            worldcover_grid = request.landcover_grid
            impact_grid = None
            provider_warnings: list[str] = []
            if ground_cover_mode == "auto" and worldcover_grid is None:
                cache_dir = (
                    Path(os.environ.get("LOCALAPPDATA", Path.home()))
                    / "MemoryMap"
                    / "landcover-cache"
                )
                landcover_request = LandCoverRequest(
                    (0.0, 0.0, frame.print_width_mm, frame.print_height_mm),
                    terrain_grid_spec.rows,
                    terrain_grid_spec.columns,
                    (bbox[2], bbox[0], bbox[3], bbox[1]),
                )
                try:
                    worldcover_grid = WorldCoverProvider(cache_dir / "worldcover").get_land_cover(
                        landcover_request
                    )
                except Exception as exc:
                    provider_warnings.append(f"ESA WorldCover unavailable: {exc}")
                try:
                    impact_grid = ImpactObservatoryProvider(
                        cache_dir / "impact-observatory"
                    ).get_land_cover(landcover_request)
                except Exception as exc:
                    provider_warnings.append(f"Impact Observatory land cover unavailable: {exc}")
                if provider_warnings:
                    warnings.append(
                        "; ".join(provider_warnings)
                        + (
                            "; using OSM-only exposed ground."
                            if worldcover_grid is None and impact_grid is None
                            else "; continuing with the available raster source."
                        )
                    )
            if ground_cover_mode == "auto" and (
                worldcover_grid is not None or impact_grid is not None
            ):
                available_grid = worldcover_grid or impact_grid
                assert available_grid is not None
                unknown_classes = np.full(
                    available_grid.shape,
                    LandCoverClass.UNKNOWN.value,
                    dtype=np.uint8,
                )
                if worldcover_grid is None:
                    worldcover_grid = LandCoverGrid(
                        unknown_classes,
                        available_grid.bounds_mm,
                        available_grid.provenance,
                    )
                if impact_grid is None:
                    impact_grid = LandCoverGrid(
                        unknown_classes,
                        available_grid.bounds_mm,
                        available_grid.provenance,
                    )
                mapped_water_mask = rasterize_geometry_mask(available_grid, water_geometries)
                osm_exposed_mask = rasterize_geometry_mask(available_grid, exposed_land)
                evidence_buffer_mm = 60.0 * frame.mm_per_meter
                evidence_geometries = (
                    []
                    if request.landcover_grid is not None
                    else download_coastal_exposure_evidence(
                        bbox=bbox,
                        center_lat=frame.center_lat,
                        center_lon=frame.center_lon,
                        transform=transform,
                        map_width_mm=frame.print_width_mm,
                        map_height_mm=frame.print_height_mm,
                        buffer_mm=evidence_buffer_mm,
                        radius_m=radius,
                    )
                )
                evidence_result = fuse_coastal_evidence(
                    worldcover_grid,
                    impact_grid,
                    mapped_water=mapped_water_mask,
                    osm_exposed=osm_exposed_mask,
                    cliff_or_outcrop=rasterize_geometry_mask(available_grid, evidence_geometries),
                    cell_size_m=(
                        frame.coverage_width_m / available_grid.shape[1],
                        frame.coverage_height_m / available_grid.shape[0],
                    ),
                    coastal_distance_m=float(config.get("ground_cover_coastal_distance_m", 1000.0)),
                    sensitivity=str(config.get("ground_cover_sensitivity", "balanced")),
                )
                fused_grid = evidence_result.grid
                cleaned_mask = cleanup_exposed_mask(
                    fused_grid,
                    excluded_water=mapped_water_mask,
                    sensitivity=str(config.get("ground_cover_sensitivity", "balanced")),
                )
                cleaned_classes = np.full(
                    fused_grid.shape,
                    LandCoverClass.UNKNOWN.value,
                    dtype=np.uint8,
                )
                cleaned_classes[cleaned_mask] = LandCoverClass.BARE.value
                cleaned_grid = LandCoverGrid(
                    cleaned_classes, fused_grid.bounds_mm, fused_grid.provenance
                )
                raster_exposed = polygonize_exposed_mask(
                    cleaned_grid,
                ).intersection(printable)
                exposed_land = list(osm_exposed_land)
                if not raster_exposed.is_empty:
                    exposed_land.append(raster_exposed)
                exposed_cells = int(np.count_nonzero(cleaned_mask))
                cell_count = fused_grid.classes.size
                ground_cover_stats = {
                    "mode": ground_cover_mode,
                    "sensitivity": str(config.get("ground_cover_sensitivity", "balanced")),
                    "provider": fused_grid.provenance.provider,
                    "dataset": fused_grid.provenance.dataset,
                    "edition": fused_grid.provenance.edition,
                    "cached": fused_grid.provenance.cached,
                    "details": dict(fused_grid.provenance.details),
                    "contributions": dict(evidence_result.contribution_counts),
                    "osm_exposed_polygon_area_mm2": float(
                        unary_union(osm_exposed_land).area if osm_exposed_land else 0.0
                    ),
                    "rows": fused_grid.shape[0],
                    "columns": fused_grid.shape[1],
                    "exposed_cells": exposed_cells,
                    "exposed_percent": 100.0 * exposed_cells / cell_count,
                    "vegetation_percent": float(
                        100.0
                        * np.count_nonzero(fused_grid.classes == LandCoverClass.VEGETATION.value)
                        / cell_count
                    ),
                    "water_percent": float(
                        100.0
                        * np.count_nonzero(fused_grid.classes == LandCoverClass.WATER.value)
                        / cell_count
                    ),
                }
            else:
                ground_cover_stats = {
                    "mode": ground_cover_mode,
                    "provider": "osm" if ground_cover_mode != "off" else None,
                }
            if (
                bool(config.get("exposed_land_enabled", True))
                and ground_cover_mode != "off"
                and not exposed_land
            ):
                warnings.append(
                    "No mapped beach, sand, rock, or other exposed-land polygons "
                    "were returned; the landscape surface remains green in those areas."
                )
            green_region = landscape_surface_region(
                frame.print_width_mm,
                frame.print_height_mm,
                frame.margin_mm,
                water_geometries=water_geometries,
                exposed_geometries=exposed_land,
            )
            landscape_mesh = build_conformal_surface_skin(
                terrain_surface,
                green_region,
                visible_thickness_mm=surface_skin_thickness_mm,
                embed_depth_mm=float(config.get("feature_embed_depth", 0.2)),
                clip_region=printable,
                flat_margin_mm=frame.margin_mm,
            )
    else:
        base_mesh = (
            build_base_plate(frame.print_width_mm, frame.print_height_mm, request.base_thickness_mm)
            if request.include_base
            else None
        )
    feature_support_at = (
        _feature_support_sampler(
            terrain_surface,
            water_bodies,
            frame.margin_mm,
        )
        if terrain_surface is not None
        else None
    )
    route_mesh = None
    if request.include_route:
        route_polygon = buffered_polygon_from_points(scaled, request.route_width_mm).intersection(
            printable
        )
        if not route_polygon.is_valid:
            route_polygon, valid, explanation = repair_polygon(route_polygon)
            if not valid:
                warnings.append(f"Route polygon repair failed: {explanation}")
        route_mesh = _route_mesh(route_polygon, route_extrusion_mm, z_offset)
        if route_mesh is not None and feature_support_at is not None:
            route_mesh = refine_mesh_edges(
                route_mesh,
                float(config.get("route_mesh_max_edge_mm", 2.4)),
            )
            route_mesh = drape_route_mesh(
                route_mesh,
                LineString(scaled),
                feature_support_at,
                route_width_mm=request.route_width_mm,
                visible_height_mm=route_height_mm,
                smoothing_distance_mm=float(config.get("route_terrain_smoothing_distance_mm", 1.5)),
                maximum_profile_slope=float(
                    config.get(
                        "route_profile_max_slope",
                        route_slope_for_layer_height(
                            float(config.get("route_layer_height_mm", 0.16))
                        ),
                    )
                ),
            )
        if route_mesh is None:
            warnings.append("The route does not intersect the printable frame.")
    progress(25, "Route mesh complete")

    marker_mode = RouteMarkerMode.parse(config.get("route_markers", "none"))
    start_marker_mesh = None
    finish_marker_mesh = None
    if request.include_route and marker_mode is not RouteMarkerMode.NONE:
        marker_diameter_mm = float(config.get("route_marker_diameter_mm", 3.2))
        support_at = feature_support_at
        if support_at is None:
            support_height = request.base_thickness_mm if request.include_base else 0.0

            def support_at(x_values, _y_values):
                return np.full(np.asarray(x_values).shape, support_height, dtype=float)

        for marker_name, center in marker_centers(
            scaled[0], scaled[-1], marker_mode, marker_diameter_mm
        ):
            marker_mesh, reason = build_route_marker(
                center,
                support_at,
                printable,
                diameter_mm=marker_diameter_mm,
                visible_height_mm=float(config.get("route_marker_height_mm", 1.0)),
                embed_depth_mm=feature_embed_mm,
                sections=int(config.get("route_marker_sections", 32)),
            )
            if marker_mesh is None:
                warnings.append(f"{marker_name.title()} marker omitted: {reason}.")
            elif marker_name == "start":
                start_marker_mesh = marker_mesh
            else:
                finish_marker_mesh = marker_mesh

    grouping_road_polygons = []
    road_source_stats = {}
    road_processing_stats = {}
    city_internal_roads = (
        request.include_buildings and config["building_appearance_mode"] == "city_relief"
    )
    unioned_roads = None
    roads_mesh = None
    road_class_meshes = {}
    if request.include_roads or city_internal_roads:
        default_road_style = resolve_road_style(style_profile)
        if landscape_style:
            road_types = config.get("landscape_road_types", default_road_style.road_types)
            road_widths = config.get("landscape_road_widths", default_road_style.road_widths_mm)
            road_height_mm = float(
                config.get(
                    "landscape_road_height",
                    default_road_style.visible_height_mm,
                )
            )
            road_smoothing_types = config.get(
                "landscape_road_terrain_smoothing_types",
                default_road_style.terrain_smoothing_types,
            )
            road_smoothing_distances = config.get(
                "landscape_road_terrain_smoothing_distances_mm", {}
            )
        else:
            road_types = config.get("road_types", default_road_style.road_types)
            road_widths = config.get("road_widths", default_road_style.road_widths_mm)
            road_height_mm = float(config.get("road_height", default_road_style.visible_height_mm))
            road_smoothing_types = config.get(
                "road_terrain_smoothing_types",
                default_road_style.terrain_smoothing_types,
            )
            road_smoothing_distances = config.get("road_terrain_smoothing_distances_mm", {})
        separated_roads = bool(config.get("road_class_meshes", False)) and not landscape_style
        if separated_roads:
            from .map_styles import ROAD_CLASSES, class_widths

            widths = class_widths(config.get("road_class_widths_mm"))
            road_widths = {kind: widths[cls] for cls, kinds in ROAD_CLASSES.items() for kind in kinds}
        if bool(config.get("building_grouping_enabled", False)) and not separated_roads:
            # At marathon scale the normal 1.1 mm local streets consume nearly
            # the whole block. Keep a nozzle-width street between grouped masses.
            road_widths = dict(road_widths)
            for road_type in (
                "residential",
                "living_street",
                "unclassified",
                "service",
                "cycleway",
            ):
                road_widths[road_type] = min(float(road_widths.get(road_type, 0.4)), 0.4)
            warnings.append(
                "Building grouping uses 0.4 mm local streets and omits alleys/driveways "
                "to preserve printable neighborhood blocks."
            )
        collector = _WarningCollector()
        logging.getLogger().addHandler(collector)
        try:
            unioned_roads, roads_mesh = download_and_build_roads(
                bbox=bbox,
                center_lat=frame.center_lat,
                center_lon=frame.center_lon,
                transform=transform,
                road_types=road_types if request.include_roads else (),
                barrier_types=(
                    set(DEFAULT_CONFIG["road_types"])
                    if city_internal_roads
                    else set(DEFAULT_CONFIG["road_types"]) - set(road_types)
                    if config.get("building_grouping_enabled")
                    else ()
                ),
                barrier_polygons=grouping_road_polygons,
                road_widths=road_widths,
                road_height_mm=road_height_mm,
                acquisition_stats=road_source_stats,
                processing_stats=road_processing_stats,
                **(
                    {
                        "grouping_barrier_widths_mm": {
                            kind: (
                                road_widths.get(kind, 0.8)
                                if kind in MAJOR_ROADS
                                else print_scale.xy_resolution_mm * 0.25
                            )
                            for kind in DEFAULT_CONFIG["road_types"]
                        },
                        "local_road_region": route_polygon.buffer(3.0)
                        if route_mesh is not None
                        else box(0, 0, 0, 0),
                        "major_road_types": MAJOR_ROADS,
                        "road_heights_mm": {
                            kind: (0.6 if kind in MAJOR_ROADS else 0.3) for kind in road_types
                        },
                    }
                    if config["building_appearance_mode"] == "city_relief" and not separated_roads
                    else {}
                ),
                **({"class_meshes_out": road_class_meshes,
                    "adaptive_streets": bool(config.get("adaptive_streets", False)),
                    "street_spacing_mm": float(config.get("street_spacing_mm", .7))}
                   if separated_roads else {}),
                network_type=str(config.get("road_network_type", "all")),
                map_width_mm=frame.print_width_mm,
                map_height_mm=frame.print_height_mm,
                margin_mm=frame.margin_mm,
                debug=bool(config.get("roads_debug", False)),
                z_offset=z_offset,
                embed_depth_mm=feature_embed_mm,
                radius_m=radius,
                roads_file=str(request.roads_file) if request.roads_file else None,
                terrain_smoothing_types=road_smoothing_types,
                excluded_service_types=(
                    set(config.get("excluded_road_service_types", ())) | {"alley", "driveway"}
                    if bool(config.get("building_grouping_enabled", False))
                    else config.get("excluded_road_service_types", ())
                ),
                excluded_access=config.get("excluded_road_access", ()),
                priority_region=route_polygon if route_mesh is not None else None,
            )
        except Exception as exc:
            error = LayerGenerationError(
                "roads" if request.include_roads else "buildings",
                "Street geometry processing failed. No new model was exported.",
            )
            error.processing_stats = {"acquisition": road_source_stats, "meshing": road_processing_stats}
            raise error from exc
        finally:
            logging.getLogger().removeHandler(collector)
        if (
            request.include_roads
            and (
                road_source_stats.get("state") == "failed"
                or road_processing_stats.get("failed_polygon_count", 0)
                or road_processing_stats.get("processing_error")
                or (unioned_roads is not None and not unioned_roads.is_empty and roads_mesh is None)
            )
        ):
            error = LayerGenerationError(
                "roads",
                "Road acquisition or processing failed; "
                f"{road_processing_stats.get('failed_polygon_count', 0)} polygon fragments failed. "
                "No new model was exported; retry after resolving the road geometry.",
            )
            error.processing_stats = {"acquisition": road_source_stats, "meshing": road_processing_stats}
            raise error
        if roads_mesh is None and request.include_roads:
            warnings.append("No road geometry was available inside the selected frame.")
            warnings.extend(f"Road detail: {message}" for message in collector.messages[-4:])
        elif roads_mesh is not None and feature_support_at is not None and road_class_meshes:
            road_class_meshes = {cls: drape_mesh(mesh, feature_support_at)
                                 for cls, mesh in road_class_meshes.items()}
            roads_mesh = concatenate(list(road_class_meshes.values()))
        elif roads_mesh is not None and feature_support_at is not None:
            smoothing_region = roads_mesh.metadata.pop("terrain_smoothing_region", None)
            profile_corridors = roads_mesh.metadata.pop("terrain_profile_corridors", ())
            if smoothing_region is not None and profile_corridors:
                roads_mesh = refine_mesh_edges(
                    roads_mesh,
                    float(config.get("road_terrain_max_edge_mm", 4.0)),
                    region=smoothing_region,
                    maximum_iterations=int(config.get("road_terrain_refinement_passes", 5)),
                    allow_partial=True,
                )
                roads_mesh.metadata.pop("edge_refinement_incomplete", None)
                roads_mesh = drape_road_mesh(
                    roads_mesh,
                    feature_support_at,
                    profile_corridors,
                    visible_height_mm=road_height_mm,
                    minimum_visible_height_mm=float(
                        config.get("road_terrain_min_visible_height_mm", 0.4)
                    ),
                    smoothing_distances_mm=road_smoothing_distances,
                )
            else:
                roads_mesh = drape_mesh(
                    roads_mesh,
                    feature_support_at,
                    smooth_top_region=smoothing_region,
                    smoothing_radius_mm=float(config.get("road_terrain_smoothing_radius_mm", 2.0)),
                    minimum_visible_height_mm=float(
                        config.get("road_terrain_min_visible_height_mm", 0.4)
                    ),
                )
    progress(55, "Preparing building barriers")
    building_barriers_started = perf_counter()

    route_exclusion = None
    if print_optimized and request.include_route:
        protected_route = [route_polygon]
        for marker in (start_marker_mesh, finish_marker_mesh):
            if marker is not None:
                # Conservative projection of marker geometry protects every overhang.
                protected_route.append(MultiPoint(marker.vertices[:, :2]).convex_hull)
        route_exclusion = unary_union(protected_route).buffer(print_scale.route_clearance_mm)
    grouping_options = None
    grouping_barriers = None
    if request.include_buildings and bool(config.get("building_grouping_enabled", False)):
        if (not city_internal_roads and not request.include_roads) or (
            unioned_roads is None
            and not grouping_road_polygons
            and road_source_stats.get("state") not in {"complete", "empty"}
        ):
            warnings.append(
                "Building grouping skipped: road barriers are unavailable. Enable Roads."
            )
        else:
            # Read water even when its visible layer is disabled. Missing water
            # data must never be mistaken for permission to bridge a river.
            try:
                barriers_water = water_geometries
                if not (bool(config.get("terrain_enabled")) and bool(config.get("water_enabled"))):
                    barriers_water = request.water_polygons
                    if barriers_water is None:
                        barriers_water = download_water_polygons(
                            bbox=bbox,
                            center_lat=frame.center_lat,
                            center_lon=frame.center_lon,
                            transform=transform,
                            map_width_mm=frame.print_width_mm,
                            map_height_mm=frame.print_height_mm,
                            radius_m=radius,
                            water_file=request.water_file,
                            strict=True,
                            minimum_waterway_width_mm=float(
                                config.get("minimum_waterway_width_mm", 0.8)
                            ),
                        )
                if config["building_appearance_mode"] == "city_relief":
                    from .city_relief import open_space_polygons

                    spaces = open_space_polygons(
                        bbox,
                        transform,
                        frame.center_lat,
                        frame.center_lon,
                        request.open_space_polygons,
                    )
                    grouping_road_polygons.extend(spaces)
                route_barrier = (
                    route_exclusion
                    if route_exclusion is not None
                    else buffered_polygon_from_points(scaled, request.route_width_mm)
                )
                grouping_barriers = unary_union(
                    [
                        p
                        for p in [
                            unioned_roads,
                            *grouping_road_polygons,
                            route_barrier,
                            *barriers_water,
                        ]
                        if p is not None
                    ]
                )
                grouping_options = {
                    "width_mm": float(config.get("building_grouping_width_mm", 0.8)),
                    "gap_mm": float(config.get("building_grouping_gap_mm", 0.4)),
                    "span_mm": float(config.get("building_grouping_span_mm", 4.0)),
                    "max_height_mm": float(config.get("building_grouping_max_height_mm", 3.0)),
                }
            except Exception as exc:
                if city_internal_roads:
                    raise LayerGenerationError(
                        "buildings",
                        f"City relief context acquisition or processing failed: {exc}. Retry generation.",
                    ) from exc
                if not isinstance(exc, ValueError):
                    raise
                warnings.append(f"Building grouping skipped: {exc}")

    if (
        request.include_buildings
        and config["building_appearance_mode"] == "city_relief"
        and grouping_options is None
    ):
        raise RuntimeError(
            "City relief needs complete street, water, and open-space barriers. Retry acquisition or use Existing appearance."
        )
    if city_internal_roads and road_source_stats.get("state") not in {"complete", "empty"}:
        raise LayerGenerationError(
            "roads", "City relief street acquisition failed. Retry generation."
        )
    building_filter_stats = {}
    building_generalization_stats = {
        **print_scale.diagnostics(),
        "mode": config["building_generalization_mode"],
        "thresholds_applied_to_geometry": False,
        "measurement_status": "unavailable" if request.include_buildings else "disabled",
    }
    building_generalization_stats["stage_seconds"] = {
        "barrier_preparation": perf_counter() - building_barriers_started,
    }
    unioned_buildings = None
    buildings_mesh = None
    if request.include_buildings:
        collector = _WarningCollector()
        logging.getLogger().addHandler(collector)
        try:
            unioned_buildings, buildings_mesh = download_and_build_buildings(
                bbox=bbox,
                center_lat=frame.center_lat,
                center_lon=frame.center_lon,
                transform=transform,
                map_width_mm=frame.print_width_mm,
                map_height_mm=frame.print_height_mm,
                margin_mm=frame.margin_mm,
                debug=bool(config.get("buildings_debug", False)),
                z_offset=z_offset,
                embed_depth_mm=feature_embed_mm,
                max_print_height_mm=float(config["max_print_height_mm"]),
                height_policy=height_policy,
                appearance_mode=config["building_appearance_mode"],
                city_detail=config["city_relief_detail"],
                landmark_emphasis=bool(config["city_relief_landmark_emphasis"]),
                min_building_height_mm=float(config["min_building_height_mm"]),
                building_default_height_m=float(config["building_default_height_m"]),
                building_levels_to_m=float(config["building_levels_to_m"]),
                building_max_real_height_m=float(config["building_max_real_height_m"]),
                building_clip_threshold=float(config["building_clip_threshold"]),
                radius_m=radius,
                buildings_file=str(request.buildings_file) if request.buildings_file else None,
                overlay_roads=unioned_roads,
                route_points=scaled,
                terrain_height_at=feature_support_at,
                building_scale_mm_per_m=(
                    frame.mm_per_meter * float(config.get("building_vertical_exaggeration", 1.0))
                ),
                extend_elevated_parts_to_ground=bool(
                    config.get("extend_elevated_building_parts_to_ground", True)
                ),
                diagnostics=building_records,
                grouping=grouping_options,
                grouping_barriers=grouping_barriers,
                residential_min_width_mm=float(config.get("residential_min_width_mm", 0.0)),
                filter_stats=building_filter_stats,
                print_scale_context=print_scale,
                generalization_stats=building_generalization_stats,
                generalization_mode=config["building_generalization_mode"],
                route_exclusion=route_exclusion,
                progress_callback=lambda f, message: progress(55 + int(27 * f), message),
            )
        except Exception as exc:
            raise LayerGenerationError(
                "buildings", f"Buildings failed: {exc} Retry or disable Buildings."
            ) from exc
        finally:
            logging.getLogger().removeHandler(collector)
        if buildings_mesh is None:
            warnings.append("No building geometry was available inside the selected frame.")
            warnings.extend(f"Building detail: {message}" for message in collector.messages[-4:])
    if request.include_buildings and any(
        r.get("reason") in {"mesh_failed", "transform_failed", "frame_intersection_failed"}
        for r in (building_records or [])
    ):
        raise LayerGenerationError(
            "buildings",
            "Building processing failed. No new model was exported; retry or disable Buildings.",
        )
    layer_states = {
        "terrain": "complete" if terrain_surface is not None and not flat_water_context else "disabled",
        "water": ("disabled" if not config.get("water_enabled", False) else
                  "complete" if water_mesh is not None else "empty"),
        "buildings": (
            "disabled"
            if not request.include_buildings
            else "complete"
            if buildings_mesh is not None
            else "empty"
        ),
        "roads": (
            "disabled"
            if not request.include_roads
            else "complete"
            if roads_mesh is not None
            else "empty"
        ),
        "route": (
            "disabled"
            if not request.include_route
            else "complete"
            if route_mesh is not None
            else "empty"
        ),
    }
    building_generalization_stats["layer_state"] = layer_states["buildings"]
    if buildings_mesh is not None and grouping_options is not None:
        grouping_stats = buildings_mesh.metadata.get("building_grouping", {})
        warnings.append(
            f"Building grouping: {grouping_stats.get('grouped_sources', 0)} source footprints "
            f"combined into {grouping_stats.get('groups', 0)} masses; "
            f"{grouping_stats.get('ungrouped_small_sources', 0)} small footprints remain individual. "
            "Inspect the sliced result before printing."
        )
    if building_filter_stats.get("minimum_width_mm", 0) > 0:
        warnings.append(
            f"Residential width filter omitted {building_filter_stats.get('omitted_sources', 0)} "
            f"source footprints below {building_filter_stats['minimum_width_mm']:g} mm after grouping. "
            "Unclassified and protected buildings are retained; inspect the sliced model."
        )
    if print_optimized and buildings_mesh is not None and route_exclusion is not None:
        # Include roofs and landmark enhancements, not only the input footprints.
        progress(82, "Checking building route clearance")
        route_check_started = perf_counter()
        overlap = _building_route_overlap(
            buildings_mesh.triangles[:, :, :2],
            route_exclusion,
            lambda f: progress(82 + int(2 * f), "Checking building route clearance"),
        )
        building_generalization_stats["stage_seconds"]["route_clearance_check"] = (
            perf_counter() - route_check_started
        )
        building_generalization_stats["route_exclusion_projected_overlap_mm2"] = overlap
        if overlap > 1e-6:
            raise ValueError("Optimized building geometry crosses the route clearance corridor")
    if print_optimized and buildings_mesh is not None:
        counts = building_generalization_stats.get("decisions", {})
        warnings.append(
            "Print-optimized buildings: "
            + ", ".join(
                f"{counts.get(action, 0)} {action}"
                for action in ("simplified", "grouped", "enlarged", "omitted", "unresolved")
            )
            + ". Unknown source classifications are preserved in the audit report."
        )
    logging.info(
        "BUILDING GENERALIZATION CONTEXT %s",
        json.dumps(
            building_generalization_stats,
            sort_keys=True,
            allow_nan=False,
        ),
    )
    progress(85, "Building mesh complete")

    if all(
        mesh is None
        for mesh in (
            base_mesh,
            route_mesh,
            roads_mesh,
            buildings_mesh,
            water_mesh,
            landscape_mesh,
            start_marker_mesh,
            finish_marker_mesh,
        )
    ):
        raise ValueError("No printable layers were generated")
    if request.export_model:
        exported_meshes = export_3mf(
            output_path,
            base_mesh,
            route_mesh,
            roads_mesh,
            buildings_mesh,
            water_mesh,
            landscape_mesh=landscape_mesh,
            style_profile=style_profile,
            color_preset=config.get("color_preset"),
            layer_colors=config.get("layer_colors"),
            start_marker_mesh=start_marker_mesh,
            finish_marker_mesh=finish_marker_mesh,
            **({"road_class_meshes": road_class_meshes,
                "road_class_colors": config.get("road_class_colors", {})}
               if road_class_meshes else {}),
        )
        buildings_mesh = exported_meshes["buildings"]
        water_mesh = exported_meshes["water"]
        landscape_mesh = exported_meshes["landscape"]
        if buildings_mesh is not None:
            original_faces = buildings_mesh.metadata.pop("export_original_face_indices", None)
            if original_faces is not None:
                removed_forms = remap_building_export_records(
                    building_records or [], original_faces
                )
                if building_generalization_stats is not None and building_records is not None:
                    if "output_representative_count" in building_generalization_stats:
                        building_generalization_stats["output_representative_count"] -= (
                            removed_forms
                        )
                    building_generalization_stats["height_distribution_stage"] = (
                        "before_export_cleanup"
                    )
                    building_generalization_stats["represented_source_count"] = sum(
                        bool(record.get("output_face_ranges"))
                        for record in (building_records or [])
                    )
                    building_generalization_stats["omitted_count"] = sum(
                        record.get("status") == "omitted" for record in (building_records or [])
                    )
                    building_generalization_stats["export_cleanup_omissions"] = sum(
                        record.get("reason") == "unsupported_export_shell"
                        for record in (building_records or [])
                    )
        for cleaned_layer in (water_mesh, landscape_mesh):
            if cleaned_layer is not None:
                cleaned_layer.metadata.pop("export_original_face_indices", None)
        # Reusing an output filename must never leave an older model's audit.
        output_path.with_suffix(".audit.json").unlink(missing_ok=True)
    stats = {
        "map_style": request.map_style,
        "flat_water_context": flat_water_context,
        "route_points": len(request.route.points),
        "style_profile": style_profile,
        "route_height_mm": route_height_mm,
        "route_height_source": "profile_default" if request.route_height_mm is None else "explicit",
        "flat_border_enabled": flat_border_enabled,
        "roads": _geometry_count(unioned_roads),
        "road_acquisition": road_source_stats,
        "road_processing": road_processing_stats,
        "road_classes": {key: _mesh_stats(mesh) for key, mesh in road_class_meshes.items()},
        "road_style": (
            {
                "profile": style_profile,
                "types": list(road_types),
                "height_mm": road_height_mm,
                "widths_mm": dict(road_widths),
            }
            if request.include_roads
            else None
        ),
        "ground_cover": ground_cover_stats,
        "buildings": _geometry_count(unioned_buildings),
        "building_filter": building_filter_stats,
        "building_generalization": building_generalization_stats,
        "building_grouping": (
            buildings_mesh.metadata.get("building_grouping") if buildings_mesh is not None else None
        ),
        "building_height_distribution": (
            buildings_mesh.metadata.get("building_height_distribution")
            if buildings_mesh is not None
            else None
        ),
        "terrain": (
            {
                "source": elevation_grid.source,
                "flatness_rating": terrain_surface.analysis.flatness_rating,
                "relief_mm": terrain_surface.analysis.target_relief_mm,
                "grid": {
                    "mode": (
                        "provided" if request.elevation_grid is not None else terrain_grid_spec.mode
                    ),
                    "rows": terrain_grid_spec.rows,
                    "columns": terrain_grid_spec.columns,
                    "samples": terrain_grid_spec.sample_count,
                    "target_cell_size_mm": terrain_grid_spec.target_cell_size_mm,
                    "cell_width_mm": terrain_grid_spec.cell_width_mm,
                    "cell_height_mm": terrain_grid_spec.cell_height_mm,
                    "estimated_terrain_faces": (terrain_grid_spec.estimated_terrain_faces),
                },
            }
            if terrain_surface is not None
            else None
        ),
        "layer_states": layer_states,
        "build": build_identity(),
        "layers": {
            "base": _mesh_stats(base_mesh),
            "route": _mesh_stats(route_mesh),
            "roads": _mesh_stats(roads_mesh),
            "buildings": _mesh_stats(buildings_mesh),
            "water": _mesh_stats(water_mesh),
            "landscape": _mesh_stats(landscape_mesh),
            "start_marker": _mesh_stats(start_marker_mesh),
            "finish_marker": _mesh_stats(finish_marker_mesh),
        },
    }
    if request.export_model:
        import hashlib

        report = {
            "schema_version": 2,
            "map_style_algorithm": "map-styles-v1" if request.map_style is not None else None,
            "build": build_identity(),
            "layer_states": layer_states,
            "mode": config["building_generalization_mode"],
            "algorithm_version": (
                "city-relief-v1"
                if config["building_appearance_mode"] == "city_relief"
                else "building-optimization-v1"
                if print_optimized
                else "building-heights-v1"
            ),
            "route_points_sha256": hashlib.sha256(
                json.dumps(
                    [asdict(point) for point in request.route.points], sort_keys=True
                ).encode()
            ).hexdigest(),
            "model_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            "settings": config,
            "print_scale": print_scale.diagnostics(),
            "frame": asdict(frame),
            "statistics": stats,
            "buildings": building_records,
            "warnings": warnings,
        }
        output_path.with_suffix(".audit.json").write_text(
            json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
        )
    progress(100, "3MF export complete" if request.export_model else "Meshes ready for analysis")
    return GenerationResult(
        output_path=output_path,
        warnings=warnings,
        stats=stats,
        base_mesh=base_mesh,
        route_mesh=route_mesh,
        roads_mesh=roads_mesh,
        buildings_mesh=buildings_mesh,
        water_mesh=water_mesh,
        landscape_mesh=landscape_mesh,
        start_marker_mesh=start_marker_mesh,
        finish_marker_mesh=finish_marker_mesh,
        road_class_meshes=road_class_meshes,
    )


def remap_building_export_records(records, original_faces):
    """Bind diagnostic face ranges to the cleaned, actually exported mesh."""
    import numpy as np

    indices = np.asarray(original_faces)
    removed_forms = set()
    for record in records:
        prior = record.get("output_face_ranges", [])
        ranges = []
        for start, end in prior:
            left, right = np.searchsorted(indices, [start, end])
            if right > left:
                ranges.append([int(left), int(right)])
        record["output_face_ranges"] = ranges
        if prior and not ranges:
            removed_forms.add(tuple(tuple(pair) for pair in prior))
            record.update(status="omitted", reason="unsupported_export_shell")
            record["output_print_geometry"] = None
        elif prior != ranges:
            record["export_cleanup"] = "face_ranges_remapped"
    return len(removed_forms)


def _geometry_count(geometry: Any | None) -> int:
    if geometry is None or geometry.is_empty:
        return 0
    if hasattr(geometry, "geoms"):
        return len([part for part in geometry.geoms if not part.is_empty])
    return 1
