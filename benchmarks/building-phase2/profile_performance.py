"""Offline exported-shell policy benchmark with real Peachtree roads; not full generation."""

import argparse
import cProfile
import hashlib
import inspect
import json
import platform
import pstats
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
import shapely
from shapely.geometry import box, shape

from memorymap_pipeline.building_optimization import optimize_buildings
from memorymap_pipeline.buildings import BuildingDimensions
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.print_scale import PrintScaleContext


def roads_from_export(source, cache):
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    cache_source = cache.with_suffix(".source.sha256")
    if cache.exists() and cache_source.exists() and cache_source.read_text() == source_hash:
        return shapely.from_wkb(cache.read_bytes())
    with zipfile.ZipFile(source) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    ns = {"m": root.tag.split("}")[0][1:]}
    road = next(o for o in root.findall(".//m:object", ns) if o.get("name") == "Roads_Black")
    vertices = np.array(
        [[float(v.get(k)) for k in ("x", "y", "z")] for v in road.findall(".//m:vertex", ns)]
    )
    faces = np.array(
        [[int(f.get(k)) for k in ("v1", "v2", "v3")] for f in road.findall(".//m:triangle", ns)]
    )
    tri = vertices[faces]
    top = faces[np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 2] > 1e-12]
    edges = np.sort(np.concatenate([top[:, [0, 1]], top[:, [1, 2]], top[:, [2, 0]]]), axis=1)
    edges, count = np.unique(edges, axis=0, return_counts=True)
    boundary = shapely.linestrings(vertices[edges[count == 1], :2])
    roads = shapely.build_area(shapely.union_all(boundary))
    assert roads.is_valid and roads.area > 0
    assert abs(roads.area - shapely.area(shapely.polygons(vertices[top, :2])).sum()) < 1e-5
    cache.write_bytes(shapely.to_wkb(roads))
    cache_source.write_text(source_hash)
    return roads


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    root = Path("benchmarks/outputs/peachtree-audit/saved-24-1")
    out = Path("benchmarks/outputs/building-performance")
    out.mkdir(parents=True, exist_ok=True)
    roads = roads_from_export(root / "source-86acd5d422d2.3mf", out / "roads.wkb")
    features = json.loads((root / "building-original-shells.geojson").read_text())["features"]
    if args.limit:
        features = [features[i] for i in np.linspace(0, len(features) - 1, args.limit, dtype=int)]
    route = shape(json.loads((root / "route-footprint.geojson").read_text()))
    elements, heights = [], []
    for f in features:
        p = f["properties"]
        dims = BuildingDimensions(
            max(0, p["z_min_mm"]), 6, 0, "flat", "along", height_source="export_proxy"
        )
        elements.append((shape(f["geometry"]), dims, p["z_min_mm"] > 0.03, None))
        heights.append(p["z_max_mm"])
    ctx = PrintScaleContext.from_frame(
        MapFrame(0, 0, 190, 240, 190, 240),
        {"line_width_mm": 0.42, "building_generalization_mode": "print_optimized"},
    )
    print(
        f"Profiling {len(elements)} shells; road vertices={shapely.get_num_coordinates(roads)}",
        flush=True,
    )
    timings = {}
    extra = (
        {"timings": timings}
        if "timings" in inspect.signature(optimize_buildings).parameters
        else {}
    )
    profiler = cProfile.Profile()
    start = time.perf_counter()
    profiler.enable()
    result = optimize_buildings(
        elements,
        heights,
        ctx,
        allowed_region=box(0, 0, 190, 240),
        barriers=roads,
        route_exclusion=route.buffer(ctx.route_clearance_mm),
        allow_grouping=True,
        **extra,
    )
    profiler.disable()
    elapsed = time.perf_counter() - start
    profiler.dump_stats(str(out / f"{args.label}.prof"))
    with (out / f"{args.label}-profile.txt").open("w") as stream:
        pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(35)
    fingerprint = hashlib.sha256()
    for p in result.footprints:
        fingerprint.update(p.wkb)
    for g in result.groups:
        fingerprint.update(str(g.members).encode())
        fingerprint.update(g.geometry.wkb)
    fingerprint.update(json.dumps(result.decisions, sort_keys=True).encode())
    fingerprint.update(json.dumps(result.group_heights, sort_keys=True).encode())
    report = {
        "label": args.label,
        "shells": len(elements),
        "elapsed_seconds": elapsed,
        "groups": len(result.groups),
        "omitted": len(result.omitted),
        "stage_seconds": timings,
        "road_coordinates": int(shapely.get_num_coordinates(roads)),
        "fingerprint": fingerprint.hexdigest(),
        "python": platform.python_version(),
        "shapely": shapely.__version__,
        "geos": shapely.geos_version_string,
        "platform": platform.platform(),
        "decisions": {
            a: sum(d["action"] == a for d in result.decisions)
            for a in ("preserved", "simplified", "grouped", "enlarged", "omitted", "unresolved")
        },
        "limitations": [
            "Exported shells lack original tags/IDs and parent dependencies",
            "Real exported roads; no original water data available",
            "Grouping enabled; no download, extrusion, terrain, export or slicer timing",
        ],
    }
    (out / f"{args.label}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
