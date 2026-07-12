from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import load_config
from .gpx_loader import load_route_from_gpx
from .mesh import build_base_plate, center_meshes_to_base, export_3mf, route_mesh_from_polygon
from .projection import (
    project_points,
    compute_normalize_center_transform,
    apply_transform,
)
from .geometry import buffered_polygon_from_points, validate_polygon, repair_polygon
from .roads import download_and_build_roads
from .buildings import download_and_build_buildings
import matplotlib.pyplot as plt


logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert a GPX route into a Bambu-ready 3MF memory map")
    parser.add_argument("input_gpx", type=Path, help="Path to the input GPX file")
    parser.add_argument("output_3mf", type=Path, help="Path to the output 3MF file")
    parser.add_argument("--config", type=Path, default=None, help="Path to a JSON configuration file")
    parser.add_argument("--orientation", choices=["portrait", "landscape"], default=None, help="Map orientation (auto if omitted)")
    parser.add_argument("--route-width-mm", type=float, default=None, help="Route width in millimeters")
    parser.add_argument("--route-height-mm", type=float, default=None, help="Raised route height in millimeters")
    parser.add_argument("--base-thickness-mm", type=float, default=None, help="Base plate thickness in millimeters")
    parser.add_argument("--no-base", dest="include_base", action="store_false", help="Do not include the base plate in the exported 3MF")
    parser.add_argument("--margin-mm", type=float, default=None, help="Margin from the map edge in millimeters")
    parser.add_argument("--roads-file", type=Path, default=None, help="Path to a local roads GeoJSON/GeoPackage to use instead of downloading OSM")
    parser.add_argument("--buildings-file", type=Path, default=None, help="Path to a local buildings GeoJSON/GeoPackage to use instead of downloading OSM")
    parser.add_argument("--no-roads", dest="include_roads", action="store_false", help="Do not include road network in the exported 3MF")
    parser.add_argument("--no-buildings", dest="include_buildings", action="store_false", help="Do not include building footprints in the exported 3MF")
    parser.set_defaults(include_base=True)
    parser.set_defaults(include_roads=True)
    parser.set_defaults(include_buildings=True)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config(args.config)
    route_width_mm = args.route_width_mm if args.route_width_mm is not None else config["route_width"]
    route_height_mm = args.route_height_mm if args.route_height_mm is not None else config["route_height"]
    base_thickness_mm = args.base_thickness_mm if args.base_thickness_mm is not None else config["base_thickness"]
    margin_mm = args.margin_mm if args.margin_mm is not None else config["margin"]

    route = load_route_from_gpx(args.input_gpx)

    # Use geographic center of route for symmetric projection
    latitudes = [p.latitude for p in route.points]
    longitudes = [p.longitude for p in route.points]
    center_lat = (min(latitudes) + max(latitudes)) / 2.0
    center_lon = (min(longitudes) + max(longitudes)) / 2.0

    projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)

    route_span_x = float(projected[:, 0].max() - projected[:, 0].min())
    route_span_y = float(projected[:, 1].max() - projected[:, 1].min())
    if args.orientation is None:
        orientation = "landscape" if route_span_x > route_span_y else "portrait"
    else:
        orientation = args.orientation

    map_info = config[orientation]
    map_width = float(map_info["map_width"])
    map_height = float(map_info["map_height"])

    # compute transform once and apply to any other layer (routes, roads, future layers)
    transform = compute_normalize_center_transform(projected, width_mm=map_width, height_mm=map_height, margin_mm=margin_mm)
    scaled = apply_transform(projected, transform)

    base_mesh = None
    if args.include_base:
        base_mesh = build_base_plate(map_width, map_height, thickness_mm=base_thickness_mm)

    # build buffered polygon and validate/repair
    poly = buffered_polygon_from_points(scaled, route_width_mm)
    is_valid, _explanation = validate_polygon(poly)
    if not is_valid:
        repaired, repaired_valid, repaired_explanation = repair_polygon(poly)
        if not repaired_valid:
            raise ValueError(f"Unable to repair route polygon: {repaired_explanation}")
        poly_to_use = repaired
    else:
        poly_to_use = poly

    # save before/after visualizations beside output_3mf
    out_dir = args.output_3mf.parent
    try:
        fig, ax = plt.subplots(figsize=(6, 8))
        if not poly.is_empty:
            x, y = poly.exterior.xy
            ax.fill(x, y, alpha=0.6, fc="green", ec="black")
        ax.set_aspect("equal", adjustable="box")
        fig.savefig(out_dir / "buffered_before.png", dpi=150)
        plt.close(fig)
    except Exception as exc:  # Debug rendering must not block model generation.
        logger.warning("Could not write route debug image: %s", exc)

    if not is_valid:
        try:
            fig, ax = plt.subplots(figsize=(6, 8))
            if not poly_to_use.is_empty:
                x, y = poly_to_use.exterior.xy
                ax.fill(x, y, alpha=0.6, fc="orange", ec="black")
            ax.set_aspect("equal", adjustable="box")
            fig.savefig(out_dir / "buffered_after.png", dpi=150)
            plt.close(fig)
        except Exception as exc:  # Debug rendering must not block model generation.
            logger.warning("Could not write repaired-route debug image: %s", exc)

    # Extrude overlay layers so they start at the base top plane (z=0).
    z_offset = 0.0
    route_mesh = route_mesh_from_polygon(poly_to_use, height_mm=route_height_mm, z_offset=z_offset)
    # build roads (separate body)
    roads_mesh = None
    unioned = None
    if args.include_roads:
        unioned, roads_mesh = download_and_build_roads(
            bbox=(min(latitudes), max(latitudes), min(longitudes), max(longitudes)),
            center_lat=center_lat,
            center_lon=center_lon,
            transform=transform,
            road_types=config.get("road_types", []),
            road_widths=config.get("road_widths", {}),
            road_height_mm=config.get("road_height", 0.8),
            network_type=config.get("road_network_type", "all"),
            map_width_mm=map_width,
            map_height_mm=map_height,
            margin_mm=margin_mm,
            debug=config.get("roads_debug", False),
            z_offset=z_offset,
            radius_m=config.get("road_query_radius_m", None),
            roads_file=str(args.roads_file) if args.roads_file is not None else None,
        )
    # build buildings (verification overlay)
    buildings_mesh = None
    unioned_buildings = None
    if args.include_buildings:
        buildings_file_arg = str(args.buildings_file) if args.buildings_file is not None else None
        unioned_buildings, buildings_mesh = download_and_build_buildings(
            bbox=(min(latitudes), max(latitudes), min(longitudes), max(longitudes)),
            center_lat=center_lat,
            center_lon=center_lon,
            transform=transform,
            map_width_mm=map_width,
            map_height_mm=map_height,
            margin_mm=margin_mm,
            debug=config.get("buildings_debug", False),
            z_offset=z_offset,
            max_print_height_mm=config.get("max_print_height_mm", 31.75),
            min_building_height_mm=config.get("min_building_height_mm", 0.4),
            building_default_height_m=config.get("building_default_height_m", 6.0),
            building_levels_to_m=config.get("building_levels_to_m", 3.0),
            building_max_real_height_m=config.get("building_max_real_height_m", 400.0),
            building_clip_threshold=config.get("building_clip_threshold", 0.5),
            radius_m=config.get("road_query_radius_m", None),
            buildings_file=buildings_file_arg,
            overlay_roads=unioned,
            route_points=scaled,
        )

    if base_mesh is not None:
        center_meshes_to_base([route_mesh] + ([roads_mesh] if roads_mesh is not None else []) + ([buildings_mesh] if buildings_mesh is not None else []), map_width, map_height)

    export_3mf(args.output_3mf, base_mesh, route_mesh, roads_mesh, buildings_mesh)
    print(f"Exported {args.output_3mf} (base included: {args.include_base}, orientation: {orientation}, map {map_width}x{map_height}mm, margin {margin_mm}mm)")


if __name__ == "__main__":
    main()
