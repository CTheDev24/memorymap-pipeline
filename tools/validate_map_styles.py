"""Reproducible offline style regression artifacts; no downloads or printer jobs.

Run: python tools/validate_map_styles.py --output benchmarks/outputs/map-style-v1
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import numpy as np
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memorymap_pipeline.desktop.preview import export_preview_glb
from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.map_styles import EXPERIMENTAL_WIDTHS, ROAD_CLASSES
from memorymap_pipeline.terrain import ElevationGrid


def render(result, folder):
    path = export_preview_glb(result.meshes, folder / "model.glb")
    subprocess.run([sys.executable, str(ROOT / "tools/render_city_relief_preview.py"),
                    str(path), "240", "190"], cwd=ROOT, check=True, timeout=45)


def model_components(path):
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    assert len(root.findall("m:build/m:item", ns)) == 1
    return [item.attrib["name"] for item in root.findall("m:resources/m:object", ns)
            if item.find("m:mesh", ns) is not None]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixtures = ROOT / "tests/fixtures"
    frame = MapFrame(29.76, -95.37, 180, 140, 240, 190, 5)
    # Synthetic geographic roads with all four classes, dense parallel corridors,
    # sparse grid lines and a real fixture GPX. The locations are not race references.
    features = []
    streets = [("motorway", [(5, 145), (235, 145)]),
               ("primary", [(185, 5), (185, 185)]),
               ("service", [(210, 5), (210, 185)])]
    streets += [("residential", [(5, y), (235, y)]) for y in (20, 20.2, 20.4, 45, 70, 95, 120, 165)]
    streets += [("residential", [(x, 5), (x, 185)]) for x in (30, 65, 100, 135, 165)]
    for i, (highway, points) in enumerate(streets):
        points = np.asarray(points)
        lats, lons = frame.print_to_lonlat(points[:, 0], points[:, 1])
        features.append({"type": "Feature", "properties": {"highway": highway, "name": f"Fixture street {i}"},
                         "geometry": {"type": "LineString", "coordinates": list(zip(lons, lats))}})
    roads = output / "roads.geojson"
    roads.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    building_features = []
    for i, (bounds, height) in enumerate([((45, 50, 60, 65), 4), ((110, 75, 125, 90), 7)]):
        left, bottom, right, top = bounds
        points = np.array([(left, bottom), (right, bottom), (right, top), (left, top), (left, bottom)])
        lats, lons = frame.print_to_lonlat(points[:, 0], points[:, 1])
        building_features.append({"type": "Feature", "properties": {"building": "commercial", "height": height, "id": str(i)},
                                  "geometry": {"type": "Polygon", "coordinates": [list(zip(lons, lats))]}})
    buildings = output / "buildings.geojson"
    buildings.write_text(json.dumps({"type": "FeatureCollection", "features": building_features}), encoding="utf-8")
    shutil.copy2(fixtures / "frame_route.gpx", output / "route.gpx")
    lats, lons = frame.print_to_lonlat(np.array([0, 240, 0, 240]), np.array([0, 0, 190, 190]))
    x, y = np.meshgrid(np.linspace(0, 1, 17), np.linspace(0, 1, 13))
    values = 700 * np.exp(-((x - .65) ** 2 + (y - .4) ** 2) / .025)
    values += 400 * np.exp(-((x - .4) ** 2 + (y - .75) ** 2) / .04)
    grid = ElevationGrid(values, min(lats), max(lats), min(lons), max(lons), "synthetic-regression")
    (output / "dem.json").write_text(json.dumps({"elevations_m": values.tolist(),
        "bounds": [min(lats), max(lats), min(lons), max(lons)], "source": grid.source}), encoding="utf-8")
    report = {"coverage": "Representative fixtures, not Houston/Big Sur/Chicago race generations",
              "inputs": {name: sha256((output / name).read_bytes()).hexdigest()
                         for name in ("route.gpx", "roads.geojson", "buildings.geojson", "dem.json")},
              "experimental_widths_mm": EXPERIMENTAL_WIDTHS, "cases": {},
              "acceptance": {"bambu_part_assignment": "pending_manual", "physical_print": "pending"}}
    for style in ("cityscape", "terrain", "street_grid"):
        folder = output / style
        folder.mkdir()
        config = {"ground_cover_mode": "off", "exposed_land_enabled": False}
        if style == "terrain":
            config.update(terrain_max_relief_mm=12, water_enabled=True)
        if style == "street_grid":
            config["road_types"] = [kind for kinds in ROAD_CLASSES.values() for kind in kinds]
        base_request = GenerationRequest(
            load_route_from_gpx(output / "route.gpx"), frame, folder / "baseline.3mf",
            include_buildings=style == "cityscape", include_roads=style != "terrain",
            roads_file=roads, buildings_file=buildings,
            elevation_grid=grid if style == "terrain" else None,
            water_polygons=[box(0, 0, 50, 190)] if style == "terrain" else None,
            config={**config, "style_profile": "landscape" if style == "terrain" else "urban",
                    "terrain_enabled": style == "terrain"},
        )
        baseline = generate_memory_map(base_request)
        candidate_request = replace(base_request, output_path=folder / "candidate.3mf", map_style=style)
        started = perf_counter()
        candidate = generate_memory_map(candidate_request)
        seconds = perf_counter() - started
        invariants = {}
        for name in ("base_mesh", "route_mesh", "buildings_mesh", "landscape_mesh", "water_mesh"):
            a, b = getattr(baseline, name), getattr(candidate, name)
            equal = a is None and b is None or (a is not None and b is not None
                     and np.array_equal(a.vertices, b.vertices) and np.array_equal(a.faces, b.faces))
            invariants[name] = bool(equal)
            if not equal:
                raise RuntimeError(f"Unexpected reference regression: {style}/{name}")
        export_preview_glb(candidate.meshes, folder / "candidate.glb")
        render(candidate, folder)
        baseline_folder = folder / "baseline"
        baseline_folder.mkdir()
        render(baseline, baseline_folder)
        model_hash = sha256(candidate.output_path.read_bytes()).hexdigest()
        audit = json.loads(candidate.output_path.with_suffix(".audit.json").read_text())
        assert model_hash == audit["model_sha256"]
        components = model_components(candidate.output_path)
        if style != "terrain":
            assert {"Roads_Motorways", "Roads_Main", "Roads_Local", "Roads_Service"} <= set(components)
        report["cases"][style] = {"seconds": seconds, "invariants": invariants,
                                  "components": components, "model_sha256": model_hash,
                                  "statistics": candidate.stats}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: {"seconds": value["seconds"], "invariants": value["invariants"],
                          "components": value["components"]} for key, value in report["cases"].items()}, indent=2))


if __name__ == "__main__":
    main()
