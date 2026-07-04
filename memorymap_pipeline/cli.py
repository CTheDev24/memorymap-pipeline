from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .gpx_loader import load_route_from_gpx
from .mesh import build_base_plate, export_3mf, route_mesh_from_polygon
from .projection import normalize_scale_and_center_points, project_points
from .geometry import buffered_polygon_from_points, validate_polygon, repair_polygon
import matplotlib.pyplot as plt


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
    parser.set_defaults(include_base=True)
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
    projected = project_points(route.points, center_lat=route.points[0].latitude, center_lon=route.points[0].longitude)

    route_span_x = float(projected[:, 0].max() - projected[:, 0].min())
    route_span_y = float(projected[:, 1].max() - projected[:, 1].min())
    if args.orientation is None:
        orientation = "landscape" if route_span_x > route_span_y else "portrait"
    else:
        orientation = args.orientation

    map_info = config[orientation]
    map_width = float(map_info["map_width"])
    map_height = float(map_info["map_height"])

    scaled = normalize_scale_and_center_points(projected, width_mm=map_width, height_mm=map_height, margin_mm=margin_mm)

    base_mesh = None
    if args.include_base:
        base_mesh = build_base_plate(map_width, map_height, thickness_mm=base_thickness_mm)

    # build buffered polygon and validate/repair
    poly = buffered_polygon_from_points(scaled, route_width_mm)
    is_valid, explanation = validate_polygon(poly)
    if not is_valid:
        repaired, repaired_valid, repaired_explanation = repair_polygon(poly)
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
    except Exception:
        pass

    if not is_valid:
        try:
            fig, ax = plt.subplots(figsize=(6, 8))
            if not poly_to_use.is_empty:
                x, y = poly_to_use.exterior.xy
                ax.fill(x, y, alpha=0.6, fc="orange", ec="black")
            ax.set_aspect("equal", adjustable="box")
            fig.savefig(out_dir / "buffered_after.png", dpi=150)
            plt.close(fig)
        except Exception:
            pass

    # extrude polygon to create route mesh sitting on top of the base plate
    z_offset = base_thickness_mm if args.include_base else 0.0
    route_mesh = route_mesh_from_polygon(poly_to_use, height_mm=route_height_mm, z_offset=z_offset)

    export_3mf(args.output_3mf, base_mesh, route_mesh)
    print(f"Exported {args.output_3mf} (base included: {args.include_base}, orientation: {orientation}, map {map_width}x{map_height}mm, margin {margin_mm}mm)")


if __name__ == "__main__":
    main()
