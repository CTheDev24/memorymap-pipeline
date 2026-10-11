"""Compare an Existing road-only change to its exact frozen extrusion input."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memorymap_pipeline import generation, roads
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame

parser = argparse.ArgumentParser()
parser.add_argument("area", choices=("downtown", "neighborhood", "landmark"))
args = parser.parse_args()
root = Path("benchmarks/outputs/city-relief")
manifest = json.loads((root / f"supported-existing-{args.area}-manifest.json").read_text())
request = dict(manifest["request"])
request.pop("parks_file", None)
request["route"] = load_route_from_gpx(request.pop("gpx_path"))
request["frame"] = MapFrame(**request["frame"])
request["include_buildings"] = False
request["output_path"] = root / "supported-outline-capture-not-exported.3mf"
assert not request["output_path"].exists()
outlines = []
original_regions = roads._extrude_road_regions
original_download = generation.download_and_build_roads


class Captured(Exception):
    pass


def capture_regions(regions, **kwargs):
    assert not kwargs["hierarchical"], "This proof is scoped to Existing roads"
    outlines.extend(
        p for region, _ in regions for p in roads._polygon_parts(region) if p.area > 1e-8
    )
    return []


def capture_download(**kwargs):
    original_download(**kwargs)
    raise Captured()


roads._extrude_road_regions = capture_regions
generation.download_and_build_roads = capture_download
try:
    generation.generate_memory_map(generation.GenerationRequest(**request))
except generation.LayerGenerationError as error:
    if not isinstance(error.__cause__, Captured):
        raise
finally:
    roads._extrude_road_regions = original_regions
    generation.download_and_build_roads = original_download
assert outlines, "Frozen source outline capture failed"
assert not request["output_path"].exists(), "Outline capture unexpectedly exported a model"
expected = unary_union(outlines)


def footprint(model):
    mesh = trimesh.load_scene(model, process=False).geometry["Roads_Black"]
    triangles = mesh.triangles
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    return unary_union([Polygon(t[:, :2]) for t in triangles[normals[:, 2] > 0]])


old = footprint(root / f"prototypes/{args.area}/existing/sample.3mf")
new = footprint(root / f"supported-existing/{args.area}/sample.3mf")
report = {
    "area": args.area,
    "expected_area_mm2": expected.area,
    "old_area_mm2": old.area,
    "new_area_mm2": new.area,
    "old_source_symmetric_difference_mm2": old.symmetric_difference(expected).area,
    "new_source_symmetric_difference_mm2": new.symmetric_difference(expected).area,
    "old_new_symmetric_difference_mm2": old.symmetric_difference(new).area,
    "old_new_hausdorff_mm": old.hausdorff_distance(new),
    "tolerance_mm2": 1e-6,
    "scope": "Frozen Existing road regions after the unchanged buffering/clipping policy; no buildings regenerated or export written by outline capture",
}
report["passed"] = report["new_source_symmetric_difference_mm2"] <= report["tolerance_mm2"]
(root / f"supported-existing-{args.area}-road-difference.json").write_text(
    json.dumps(report, indent=2)
)
print(json.dumps(report, indent=2))
assert report["passed"], "Existing road repair does not match frozen extrusion input"
