from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
import logging
import os
from pathlib import Path
from typing import Any, Callable

import numpy as np
from shapely import contains_xy
from shapely.geometry import LineString, box
from trimesh.util import concatenate

from .buildings import download_and_build_buildings
from .config import DEFAULT_CONFIG
from .geometry import buffered_polygon_from_points, repair_polygon
from .gpx_loader import Route
from .map_frame import MapFrame
from .mesh import (
    build_base_plate,
    embedded_feature_dimensions,
    export_3mf,
    refine_mesh_edges,
    route_mesh_from_polygon,
)
from .roads import download_and_build_roads
from .terrain import (
    ElevationGrid,
    build_terrain_mesh,
    drape_mesh,
    drape_road_mesh,
    drape_route_mesh,
    terrain_surface_from_grid,
)
from .terrain_providers import TerrariumProvider, Usgs3depProvider
from .water import (
    build_terrain_mesh_with_water,
    build_vector_water_mesh,
    download_water_polygons,
    prepare_water_bodies,
)


ProgressCallback = Callable[[int, str], None]


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
    route_height_mm: float = 2.0
    base_thickness_mm: float = 1.6
    config: dict[str, Any] = field(default_factory=dict)
    roads_file: str | Path | None = None
    buildings_file: str | Path | None = None
    elevation_grid: ElevationGrid | None = None
    water_polygons: list[Any] | None = None
    water_file: str | Path | None = None


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

    @property
    def meshes(self) -> dict[str, Any]:
        return {
            name: mesh
            for name, mesh in {
                "base": self.base_mesh,
                "route": self.route_mesh,
                "roads": self.roads_mesh,
                "buildings": self.buildings_mesh,
                "water": self.water_mesh,
            }.items()
            if mesh is not None
        }


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
    lat_delta = radius / 111319.49
    lon_delta = radius / (111319.49 * max(1e-6, math.cos(math.radians(frame.center_lat))))
    return (
        frame.center_lat - lat_delta,
        frame.center_lat + lat_delta,
        frame.center_lon - lon_delta,
        frame.center_lon + lon_delta,
    ), radius


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
    terrain_surface: Any, water_bodies: list[Any]
) -> Callable[[Any, Any], np.ndarray]:
    """Sample the actual printable surface, including recessed water tops."""
    if not water_bodies:
        return terrain_surface.sample

    buffered_water = [
        (body.geometry.buffer(1e-7), float(body.level_mm)) for body in water_bodies
    ]

    def sample(x_mm, y_mm):
        x = np.asarray(x_mm, dtype=float)
        y = np.asarray(y_mm, dtype=float)
        heights = np.asarray(terrain_surface.sample(x, y), dtype=float).copy()
        for geometry, level in buffered_water:
            heights = np.where(contains_xy(geometry, x, y), level, heights)
        return heights

    return sample


def _mesh_stats(mesh: Any | None) -> dict[str, int] | None:
    if mesh is None:
        return None
    return {"vertices": int(len(mesh.vertices)), "faces": int(len(mesh.faces))}


def generate_memory_map(
    request: GenerationRequest,
    progress_callback: ProgressCallback | None = None,
) -> GenerationResult:
    def progress(value: int, message: str) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(value, message)
        except TypeError:
            progress_callback(value)  # type: ignore[misc,call-arg]

    if len(request.route.points) < 2:
        raise ValueError("At least two route points are required")
    if min(request.route_width_mm, request.route_height_mm, request.base_thickness_mm) <= 0:
        raise ValueError("Mesh dimensions must be positive")

    progress(0, "Preparing print frame")
    _configure_packaged_networking()
    frame = request.frame
    config = _merged_config(request.config)
    output_path = Path(request.output_path)
    scaled = frame.transform_points(request.route.points)
    printable = box(
        frame.margin_mm,
        frame.margin_mm,
        frame.print_width_mm - frame.margin_mm,
        frame.print_height_mm - frame.margin_mm,
    )
    route_extrusion_mm, z_offset, feature_embed_mm = embedded_feature_dimensions(
        request.route_height_mm,
        request.base_thickness_mm if request.include_base else 0.0,
        float(config.get("feature_embed_depth", 0.2)),
    )
    warnings: list[str] = []
    bbox, radius = _query_bounds(frame)
    transform = {"map_frame": frame}
    terrain_surface = None
    water_mesh = None
    water_bodies = []
    if request.include_base and bool(config.get("terrain_enabled", False)):
        grid_size = int(config.get("terrain_grid_size", 96))
        elevation_grid = request.elevation_grid
        if elevation_grid is None:
            if config.get("terrain_provider", "usgs-3dep") != "usgs-3dep":
                raise ValueError(f"Unsupported terrain provider: {config.get('terrain_provider')}")
            cache_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MemoryMap" / "dem-cache"
            provider = Usgs3depProvider(
                timeout_seconds=float(config.get("terrain_request_timeout_seconds", 20.0)),
                max_attempts=int(config.get("terrain_request_attempts", 3)),
                backoff_seconds=float(config.get("terrain_retry_backoff_seconds", 0.5)),
            )
            try:
                elevation_grid = provider.fetch(
                    bbox, (grid_size, grid_size), cache_dir
                )
            except Exception as usgs_exc:
                warnings.append(
                    f"USGS terrain unavailable; trying the global DEM fallback: {usgs_exc}"
                )
                global_provider = TerrariumProvider(
                    timeout_seconds=float(
                        config.get("terrain_fallback_timeout_seconds", 15.0)
                    ),
                    max_attempts=int(config.get("terrain_fallback_attempts", 2)),
                    backoff_seconds=float(
                        config.get("terrain_retry_backoff_seconds", 0.5)
                    ),
                    zoom=int(config.get("terrain_fallback_zoom", 12)),
                )
                try:
                    elevation_grid = global_provider.fetch(
                        bbox, (grid_size, grid_size), cache_dir
                    )
                except Exception as fallback_exc:
                    if not bool(config.get("terrain_flat_fallback", True)):
                        raise RuntimeError(
                            f"USGS terrain failed ({usgs_exc}); "
                            f"global DEM fallback failed ({fallback_exc})"
                        ) from fallback_exc
                    warnings.append(
                        "Global terrain fallback unavailable; using a flat base for "
                        f"this generation: {fallback_exc}"
                    )
                    south, north, west, east = bbox
                    elevation_grid = ElevationGrid(
                        np.zeros((grid_size, grid_size), dtype=float),
                        south,
                        north,
                        west,
                        east,
                        "flat-fallback",
                    )
        terrain_surface = terrain_surface_from_grid(
            elevation_grid,
            frame.print_width_mm,
            frame.print_height_mm,
            math.hypot(frame.coverage_width_m, frame.coverage_height_m),
            float(config.get("terrain_max_relief_mm", 3.0)),
            float(config.get("terrain_min_relief_mm", 1.5)),
        )
        if bool(config.get("water_enabled", False)):
            water_polygons = request.water_polygons
            if water_polygons is None:
                water_polygons = download_water_polygons(
                    bbox=bbox,
                    center_lat=frame.center_lat,
                    center_lon=frame.center_lon,
                    transform=transform,
                    map_width_mm=frame.print_width_mm,
                    map_height_mm=frame.print_height_mm,
                    radius_m=radius,
                    water_file=request.water_file,
                )
            if water_polygons:
                water_bodies = prepare_water_bodies(
                    water_polygons,
                    terrain_surface,
                    float(config.get("water_recess_mm", 0.4)),
                    minimum_height_mm=-request.base_thickness_mm
                    + float(config.get("water_base_skin_mm", 0.4))
                    + float(config.get("water_mesh_thickness_mm", 0.6)),
                    shoreline_tolerance_mm=float(
                        config.get("water_shoreline_tolerance_mm", 0.1)
                    ),
                    margin_mm=frame.margin_mm,
                )
                water_mesh = build_vector_water_mesh(
                    water_bodies,
                    float(config.get("water_mesh_thickness_mm", 0.6)),
                )
            else:
                warnings.append("Water is enabled but no water polygons were supplied.")
        base_mesh = (
            build_terrain_mesh_with_water(
                terrain_surface,
                request.base_thickness_mm,
                water_bodies,
                float(config.get("water_mesh_thickness_mm", 0.6)),
                float(config.get("water_support_overlap_mm", 0.4)),
            )
            if water_bodies
            else build_terrain_mesh(terrain_surface, request.base_thickness_mm)
        )
    else:
        base_mesh = (
            build_base_plate(frame.print_width_mm, frame.print_height_mm, request.base_thickness_mm)
            if request.include_base
            else None
        )
    feature_support_at = (
        _feature_support_sampler(terrain_surface, water_bodies)
        if terrain_surface is not None
        else None
    )
    route_mesh = None
    if request.include_route:
        route_polygon = buffered_polygon_from_points(scaled, request.route_width_mm).intersection(printable)
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
                visible_height_mm=request.route_height_mm,
                smoothing_distance_mm=float(
                    config.get("route_terrain_smoothing_distance_mm", 1.5)
                ),
            )
        if route_mesh is None:
            warnings.append("The route does not intersect the printable frame.")
    progress(25, "Route mesh complete")

    unioned_roads = None
    roads_mesh = None
    if request.include_roads:
        collector = _WarningCollector()
        logging.getLogger().addHandler(collector)
        try:
            unioned_roads, roads_mesh = download_and_build_roads(
            bbox=bbox,
            center_lat=frame.center_lat,
            center_lon=frame.center_lon,
            transform=transform,
            road_types=config["road_types"],
            road_widths=config["road_widths"],
            road_height_mm=float(config["road_height"]),
            network_type=str(config.get("road_network_type", "all")),
            map_width_mm=frame.print_width_mm,
            map_height_mm=frame.print_height_mm,
            margin_mm=frame.margin_mm,
            debug=bool(config.get("roads_debug", False)),
            z_offset=z_offset,
            embed_depth_mm=feature_embed_mm,
            radius_m=radius,
            roads_file=str(request.roads_file) if request.roads_file else None,
            terrain_smoothing_types=config.get("road_terrain_smoothing_types", ()),
            )
        finally:
            logging.getLogger().removeHandler(collector)
        if roads_mesh is None:
            warnings.append("No road geometry was available inside the selected frame.")
            warnings.extend(f"Road detail: {message}" for message in collector.messages[-4:])
        elif feature_support_at is not None:
            smoothing_region = roads_mesh.metadata.pop(
                "terrain_smoothing_region", None
            )
            profile_corridors = roads_mesh.metadata.pop(
                "terrain_profile_corridors", ()
            )
            if smoothing_region is not None and profile_corridors:
                roads_mesh = refine_mesh_edges(
                    roads_mesh,
                    float(config.get("road_terrain_max_edge_mm", 4.0)),
                    region=smoothing_region,
                    maximum_iterations=int(
                        config.get("road_terrain_refinement_passes", 5)
                    ),
                    allow_partial=True,
                )
                roads_mesh.metadata.pop("edge_refinement_incomplete", None)
                roads_mesh = drape_road_mesh(
                    roads_mesh,
                    feature_support_at,
                    profile_corridors,
                    visible_height_mm=float(config["road_height"]),
                    minimum_visible_height_mm=float(
                        config.get("road_terrain_min_visible_height_mm", 0.4)
                    ),
                    smoothing_distances_mm=config.get(
                        "road_terrain_smoothing_distances_mm", {}
                    ),
                )
            else:
                roads_mesh = drape_mesh(
                    roads_mesh,
                    feature_support_at,
                    smooth_top_region=smoothing_region,
                    smoothing_radius_mm=float(
                        config.get("road_terrain_smoothing_radius_mm", 2.0)
                    ),
                    minimum_visible_height_mm=float(
                        config.get("road_terrain_min_visible_height_mm", 0.4)
                    ),
                )
    progress(55, "Road mesh complete")

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
                min(
                    frame.printable_width_mm / frame.coverage_width_m,
                    frame.printable_height_mm / frame.coverage_height_m,
                )
                * float(config.get("building_vertical_exaggeration", 1.0))
            ),
            extend_elevated_parts_to_ground=bool(
                config.get("extend_elevated_building_parts_to_ground", True)
            ),
        )
        finally:
            logging.getLogger().removeHandler(collector)
        if buildings_mesh is None:
            warnings.append("No building geometry was available inside the selected frame.")
            warnings.extend(f"Building detail: {message}" for message in collector.messages[-4:])
    progress(85, "Building mesh complete")

    if all(mesh is None for mesh in (base_mesh, route_mesh, roads_mesh, buildings_mesh, water_mesh)):
        raise ValueError("No printable layers were generated")
    export_3mf(output_path, base_mesh, route_mesh, roads_mesh, buildings_mesh, water_mesh)
    stats = {
        "route_points": len(request.route.points),
        "roads": _geometry_count(unioned_roads),
        "buildings": _geometry_count(unioned_buildings),
        "terrain": (
            {
                "source": elevation_grid.source,
                "flatness_rating": terrain_surface.analysis.flatness_rating,
                "relief_mm": terrain_surface.analysis.target_relief_mm,
            }
            if terrain_surface is not None
            else None
        ),
        "layers": {
            "base": _mesh_stats(base_mesh),
            "route": _mesh_stats(route_mesh),
            "roads": _mesh_stats(roads_mesh),
            "buildings": _mesh_stats(buildings_mesh),
            "water": _mesh_stats(water_mesh),
        },
    }
    progress(100, "3MF export complete")
    return GenerationResult(
        output_path=output_path,
        warnings=warnings,
        stats=stats,
        base_mesh=base_mesh,
        route_mesh=route_mesh,
        roads_mesh=roads_mesh,
        buildings_mesh=buildings_mesh,
        water_mesh=water_mesh,
    )


def _geometry_count(geometry: Any | None) -> int:
    if geometry is None or geometry.is_empty:
        return 0
    if hasattr(geometry, "geoms"):
        return len([part for part in geometry.geoms if not part.is_empty])
    return 1
