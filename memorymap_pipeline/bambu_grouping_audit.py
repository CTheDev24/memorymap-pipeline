"""Measure grouped-footprint path coverage in flat Bambu benchmark specimens.

This checks one layer near each flat group top, not physical printability or full
3D coverage. Deposited widths are geometric approximations. Multiple extruders
are deliberately rejected until per-tool offsets are tracked.
"""

from __future__ import annotations

import argparse
import math
import re
import zipfile
from statistics import median
from pathlib import Path

import numpy as np
from shapely.affinity import translate
from shapely.geometry import LineString, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .footprint_benchmark import _read, _sha, _write


SAMPLED_HEIGHT_LEVELS = (
    ("lower_printable", 0.08),
    ("height_25pct", 0.25),
    ("height_50pct", 0.50),
    ("height_75pct", 0.75),
    ("near_top", 0.92),
)


def extrusion_layers(text: str):
    match = re.search(r"^; extruder_offset = ([^\r\n]+)", text, re.MULTILINE)
    if match is None or "," in match.group(1):
        raise ValueError("Audit requires an explicit single-extruder offset")
    offset = tuple(map(float, match.group(1).split("x")))
    if len(offset) != 2:
        raise ValueError("Invalid extruder offset")
    layers = {}
    x = y = old_e = 0.0
    z = None
    width = 0.42
    relative_e, relative_xy = True, False
    for line in text.splitlines():
        if line.startswith("; Z_HEIGHT: "):
            z = float(line.split(": ")[1])
            layers.setdefault(z, [])
        if line.startswith("; LINE_WIDTH: "):
            width = float(line.split(": ")[1]) or width
        code = line.split(";")[0].strip()
        command = code.split(" ")[0]
        if command in {"M82", "M83"}:
            relative_e = command == "M83"
        if command in {"G90", "G91"}:
            relative_xy = command == "G91"
        values = {k: float(v) for k, v in re.findall(r"([XYEIJ])(-?\d*\.?\d+)", code)}
        if command == "G92":
            old_e = values.get("E", old_e)
            x, y = values.get("X", x), values.get("Y", y)
        if command not in {"G0", "G1", "G2", "G3"}:
            continue
        nx = x + values.get("X", 0) if relative_xy else values.get("X", x)
        ny = y + values.get("Y", 0) if relative_xy else values.get("Y", y)
        extrusion = 0.0
        if "E" in values:
            extrusion = values["E"] if relative_e else values["E"] - old_e
            old_e = values["E"]
        arc = command in {"G2", "G3"}
        if z is not None and extrusion > 0 and (nx != x or ny != y or arc):
            points = [(x, y), (nx, ny)]
            if arc:
                if not ({"I", "J"} & values.keys()):
                    raise ValueError("Audit requires center-offset arcs (I/J)")
                cx, cy = x + values.get("I", 0), y + values.get("J", 0)
                radius = math.hypot(x - cx, y - cy)
                start, end = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
                delta = (end - start) % math.tau if command == "G3" else -((start - end) % math.tau)
                if abs(delta) < 1e-10:
                    delta = math.tau if command == "G3" else -math.tau
                steps = max(2, math.ceil(abs(delta) * radius / 0.03))
                points = [
                    (
                        cx + radius * math.cos(start + delta * i / steps),
                        cy + radius * math.sin(start + delta * i / steps),
                    )
                    for i in range(steps + 1)
                ]
                points[0], points[-1] = (x, y), (nx, ny)
            layers[z].append(LineString(points).buffer(width / 2, quad_segs=4))
        x, y = nx, ny
    return offset, layers


def _sample_targets(z_min: float, z_max: float) -> list[tuple[str, float]]:
    if z_max <= z_min:
        return [(label, z_min) for label, _fraction in SAMPLED_HEIGHT_LEVELS]
    span = z_max - z_min
    return [(label, z_min + fraction * span) for label, fraction in SAMPLED_HEIGHT_LEVELS]


def audit_run(run: Path) -> dict:
    report = _read(run / "report.json")
    with np.load(run / "layer-meshes.npz") as meshes:
        zshift = -min(float(meshes[k][:, 2].min()) for k in meshes.files if k.endswith("_vertices"))
    specimens = []
    for specimen in report["specimens"]:
        name = specimen["name"]
        project = run / "bambu-x1c-pla" / name / "sliced.3mf"
        with zipfile.ZipFile(project) as archive:
            import json

            plate = json.loads(archive.read("Metadata/plate_1.json"))
            offset, layers = extrusion_layers(archive.read("Metadata/plate_1.gcode").decode())
        x0, y0, x1, y1 = specimen["bounds_mm"]
        bbox = plate["bbox_all"]
        if abs(bbox[2] - bbox[0] - (x1 - x0)) > 0.01 or abs(bbox[3] - bbox[1] - (y1 - y0)) > 0.01:
            raise ValueError("Audit requires an unrotated specimen at its original scale")
        trees = {z: STRtree(paths) for z, paths in layers.items() if paths}
        groups = {r["group_id"]: r for r in specimen["records"] if r.get("group_id")}
        observations = []
        for group_id, record in groups.items():
            if record["cut_by_crop"]:
                continue
            top = record["grouped_height_mm"] + zshift
            eligible = [z for z in trees if zshift + 0.4 < z < top - 0.08]
            if not eligible:
                observations.append(
                    {
                        "group_id": group_id,
                        "status": "no_test_layer",
                        "source_member_count": len(record.get("group_source_ids") or []),
                        "group_dimensions_mm": {
                            "width": shape(record["output_print_geometry"]).bounds[2]
                            - shape(record["output_print_geometry"]).bounds[0],
                            "height": shape(record["output_print_geometry"]).bounds[3]
                            - shape(record["output_print_geometry"]).bounds[1],
                        },
                        "grouped_height_mm": record["grouped_height_mm"],
                    }
                )
                continue
            footprint = translate(
                shape(record["output_print_geometry"]),
                bbox[0] - x0 - offset[0],
                bbox[1] - y0 - offset[1],
            )
            z_min = min(eligible)
            z_max = max(eligible)
            sampled_levels = []
            for label, target in _sample_targets(z_min, z_max):
                z = min(eligible, key=lambda candidate: abs(candidate - target))
                paths = [layers[z][int(i)] for i in trees[z].query(footprint)]
                coverage = (
                    footprint.intersection(unary_union(paths)).area / footprint.area if paths else 0.0
                )
                sampled_levels.append(
                    {
                        "level": label,
                        "target_z_mm": target,
                        "layer_z_mm": z,
                        "covered_area_fraction": coverage,
                        "status": "paths_detected" if coverage > 0 else "no_paths_detected",
                    }
                )
            near_top = next((item for item in sampled_levels if item["level"] == "near_top"), None)
            coverages = [item["covered_area_fraction"] for item in sampled_levels]
            source_ids = record.get("group_source_ids") or []
            min_x, min_y, max_x, max_y = shape(record["output_print_geometry"]).bounds
            observations.append(
                {
                    "group_id": group_id,
                    "status": "sampled",
                    "expected_footprint_area_mm2": footprint.area,
                    "grouped_height_mm": record["grouped_height_mm"],
                    "source_member_count": len(source_ids),
                    "group_dimensions_mm": {"width": max_x - min_x, "height": max_y - min_y},
                    "sampled_levels": sampled_levels,
                    "minimum_coverage_fraction": min(coverages),
                    "median_coverage_fraction": median(coverages),
                    "near_top_coverage_fraction": (
                        near_top["covered_area_fraction"] if near_top is not None else None
                    ),
                    "any_sample_without_paths": any(
                        item["status"] == "no_paths_detected" for item in sampled_levels
                    ),
                }
            )
        specimens.append(
            {
                "specimen": name,
                "project_sha256": _sha(project),
                "extruder_offset_mm": offset,
                "interior_groups": observations,
                "crop_cut_groups": sum(r["cut_by_crop"] for r in groups.values()),
            }
        )
    return {
        "report_sha256": _sha(run / "report.json"),
        "method": "Representative sampled-height coverage (lower/25/50/75/near-top) using "
        "extrusion widths, 0.03 mm arc interpolation, and the single-extruder machine offset. "
        "Flat benchmark frames only.",
        "acceptance": "Physical print, complete-height and barrier validation remain pending",
        "specimens": specimens,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    result = audit_run(args.run)
    path = args.run / "group-toolpath-coverage.json"
    _write(path, result)
    for specimen in result["specimens"]:
        observations = specimen["interior_groups"]
        print(
            specimen["specimen"],
            len(observations),
            sum(r["status"] == "paths_detected" for r in observations),
            "with paths",
        )
    print(path)


if __name__ == "__main__":
    main()
