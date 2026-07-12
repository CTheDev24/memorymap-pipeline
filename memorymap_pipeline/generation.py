from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
import logging
import os
from pathlib import Path
from typing import Any, Callable

from shapely.geometry import box
from trimesh.util import concatenate

from .buildings import download_and_build_buildings
from .config import DEFAULT_CONFIG
from .geometry import buffered_polygon_from_points, repair_polygon
from .gpx_loader import Route
from .map_frame import MapFrame
from .mesh import build_base_plate, export_3mf, route_mesh_from_polygon
from .roads import download_and_build_roads


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
    base_thickness_mm: float = 1.0
    config: dict[str, Any] = field(default_factory=dict)
    roads_file: str | Path | None = None
    buildings_file: str | Path | None = None


@dataclass
class GenerationResult:
    output_path: Path
    warnings: list[str]
    stats: dict[str, Any]
    base_mesh: Any | None = None
    route_mesh: Any | None = None
    roads_mesh: Any | None = None
    buildings_mesh: Any | None = None

    @property
    def meshes(self) -> dict[str, Any]:
        return {
            name: mesh
            for name, mesh in {
                "base": self.base_mesh,
                "route": self.route_mesh,
                "roads": self.roads_mesh,
                "buildings": self.buildings_mesh,
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
    meshes = [route_mesh_from_polygon(part, height_mm, z_offset) for part in parts if not part.is_empty]
    if not meshes:
        return None
    return meshes[0] if len(meshes) == 1 else concatenate(meshes)


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
    z_offset = request.base_thickness_mm if request.include_base else 0.0
    warnings: list[str] = []

    base_mesh = (
        build_base_plate(frame.print_width_mm, frame.print_height_mm, request.base_thickness_mm)
        if request.include_base
        else None
    )
    route_mesh = None
    if request.include_route:
        route_polygon = buffered_polygon_from_points(scaled, request.route_width_mm).intersection(printable)
        if not route_polygon.is_valid:
            route_polygon, valid, explanation = repair_polygon(route_polygon)
            if not valid:
                warnings.append(f"Route polygon repair failed: {explanation}")
        route_mesh = _route_mesh(route_polygon, request.route_height_mm, z_offset)
        if route_mesh is None:
            warnings.append("The route does not intersect the printable frame.")
    progress(25, "Route mesh complete")

    bbox, radius = _query_bounds(frame)
    transform = {"map_frame": frame}
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
            radius_m=radius,
            roads_file=str(request.roads_file) if request.roads_file else None,
            )
        finally:
            logging.getLogger().removeHandler(collector)
        if roads_mesh is None:
            warnings.append("No road geometry was available inside the selected frame.")
            warnings.extend(f"Road detail: {message}" for message in collector.messages[-4:])
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
            )
        finally:
            logging.getLogger().removeHandler(collector)
        if buildings_mesh is None:
            warnings.append("No building geometry was available inside the selected frame.")
            warnings.extend(f"Building detail: {message}" for message in collector.messages[-4:])
    progress(85, "Building mesh complete")

    if all(mesh is None for mesh in (base_mesh, route_mesh, roads_mesh, buildings_mesh)):
        raise ValueError("No printable layers were generated")
    export_3mf(output_path, base_mesh, route_mesh, roads_mesh, buildings_mesh)
    stats = {
        "route_points": len(request.route.points),
        "roads": _geometry_count(unioned_roads),
        "buildings": _geometry_count(unioned_buildings),
        "layers": {
            "base": _mesh_stats(base_mesh),
            "route": _mesh_stats(route_mesh),
            "roads": _mesh_stats(roads_mesh),
            "buildings": _mesh_stats(buildings_mesh),
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
    )


def _geometry_count(geometry: Any | None) -> int:
    if geometry is None or geometry.is_empty:
        return 0
    if hasattr(geometry, "geoms"):
        return len([part for part in geometry.geoms if not part.is_empty])
    return 1
