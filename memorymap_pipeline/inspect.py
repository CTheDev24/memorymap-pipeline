from __future__ import annotations

import os
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt

from .gpx_loader import load_route_from_gpx
from .projection import project_points, normalize_and_scale_points
from .geometry import buffered_polygon_from_points, validate_polygon, repair_polygon


def inspect_gpx(path: str | Path, route_width_mm: float = 1.2, width_mm: float = 241.0, height_mm: float = 190.0, out_dir: str | Path = "inspection_output") -> dict:
    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    route = load_route_from_gpx(path)
    projected = project_points(route.points, center_lat=route.points[0].latitude, center_lon=route.points[0].longitude)
    scaled = normalize_and_scale_points(projected, width_mm=width_mm, height_mm=height_mm)

    poly = buffered_polygon_from_points(scaled, route_width_mm)
    is_valid, explanation = validate_polygon(poly)

    result = {
        "input": str(path),
        "route_points": int(scaled.shape[0]),
        "buffered_valid": is_valid,
        "explanation": explanation,
    }

    def plot_polygon(polygon, filename):
        fig, ax = plt.subplots(figsize=(6, 8))
        if polygon.is_empty:
            ax.text(0.5, 0.5, "Empty polygon", ha="center")
        else:
            x, y = polygon.exterior.xy
            ax.fill(x, y, alpha=0.6, fc="green", ec="black")
            for interior in polygon.interiors:
                ix, iy = interior.xy
                ax.fill(ix, iy, alpha=1.0, fc="white")

            # also plot the centerline
            xs = scaled[:, 0]
            ys = scaled[:, 1]
            ax.plot(xs, ys, color="blue", linewidth=0.8)

        ax.set_aspect("equal", adjustable="box")
        ax.set_title(Path(filename).name)
        out = out_dir / filename
        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)
        return out

    image_before = plot_polygon(poly, "buffered_before.png")
    result["image_before"] = str(image_before)

    if not is_valid:
        repaired, repaired_valid, repaired_explanation = repair_polygon(poly)
        result.update({
            "repaired_valid": repaired_valid,
            "repaired_explanation": repaired_explanation,
        })
        image_after = plot_polygon(repaired, "buffered_after.png")
        result["image_after"] = str(image_after)

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Inspect buffered route polygon for validity and optionally repair it")
    parser.add_argument("gpx", help="Input GPX file")
    parser.add_argument("--route-width-mm", type=float, default=1.2)
    parser.add_argument("--out", default="inspection_output")
    args = parser.parse_args()

    res = inspect_gpx(args.gpx, route_width_mm=args.route_width_mm, out_dir=args.out)
    for k, v in res.items():
        print(f"{k}: {v}")
