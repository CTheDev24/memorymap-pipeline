"""Compare original and prepared/chunked route checks on all saved building faces."""

import json
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
from shapely import area, intersection, polygons
from shapely.geometry import shape

from memorymap_pipeline.generation import _building_route_overlap

root = Path("benchmarks/outputs/peachtree-audit/saved-24-1")
out = Path("benchmarks/outputs/building-performance")
out.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(root / "source-86acd5d422d2.3mf") as archive:
    model = ET.fromstring(archive.read("3D/3dmodel.model"))
ns = {"m": model.tag.split("}")[0][1:]}
obj = next(o for o in model.findall(".//m:object", ns) if o.get("name") == "Buildings_Verification")
vertices = np.array(
    [[float(v.get(k)) for k in ("x", "y", "z")] for v in obj.findall(".//m:vertex", ns)]
)
faces = np.array(
    [[int(f.get(k)) for k in ("v1", "v2", "v3")] for f in obj.findall(".//m:triangle", ns)]
)
triangles = vertices[faces][:, :, :2]
route_data = json.loads((root / "route-footprint.geojson").read_text())


def original_check(triangles, route):
    projected = polygons(triangles)
    projected = projected[area(projected) > 1e-12]
    return float(np.sum(area(intersection(projected, route))))


report = {"triangles": len(triangles), "repeats": 3, "results": {}}
for name, check in [("before", original_check), ("after", _building_route_overlap)]:
    samples = []
    for _ in range(3):
        route = shape(route_data).buffer(0.84)  # include preparation in each timing
        start = time.perf_counter()
        overlap = check(triangles, route)
        samples.append(time.perf_counter() - start)
    report["results"][name] = {
        "seconds": samples,
        "median_seconds": median(samples),
        "overlap_mm2": overlap,
    }
assert (
    abs(report["results"]["before"]["overlap_mm2"] - report["results"]["after"]["overlap_mm2"])
    < 1e-8
)
(out / "projection-comparison.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
