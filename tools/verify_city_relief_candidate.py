"""Independently verify a bounded coupon or roads-only native export."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memorymap_pipeline.printability import audit_printability

parser = argparse.ArgumentParser()
parser.add_argument("model", type=Path)
parser.add_argument("report", type=Path)
parser.add_argument("--previous", type=Path)
args = parser.parse_args()
audit = json.loads(args.model.with_suffix(".audit.json").read_text())
scene = trimesh.load_scene(args.model, process=False)
preview = trimesh.load_scene(args.model.with_suffix(".glb"), process=False)
names = {
    "Base_White": "base",
    "Route_Accent": "route",
    "Roads_Black": "roads",
    "Buildings_Verification": "buildings",
    "Start_Marker": "start_marker",
    "Finish_Marker": "finish_marker",
}
layers = {names[name]: mesh for name, mesh in scene.geometry.items()}
required = {layer for layer, state in audit["layer_states"].items() if state == "complete"}
checks = {}
for name, mesh in scene.geometry.items():
    view = preview.geometry[name]
    layer = names[name]
    reference = audit["statistics"]["layers"][layer]
    checks[layer] = {
        "closed": bool(mesh.is_watertight),
        "wound": bool(mesh.is_winding_consistent),
        "finite": bool(np.isfinite(mesh.vertices).all()),
        "positive_volume": bool(mesh.volume > 0),
        "audit_counts_match": len(mesh.faces) == reference["faces"]
        and len(mesh.vertices) == reference["vertices"],
        "preview_faces_match": bool(np.array_equal(mesh.faces, view.faces)),
        "preview_vertices_match_float32": bool(
            np.array_equal(mesh.vertices.astype(np.float32), view.vertices)
        ),
    }
support = audit_printability(layers)
records = audit.get("buildings", [])
represented = [record for record in records if record.get("output_face_ranges")]
building_faces = len(layers["buildings"].faces) if "buildings" in layers else 0
source_ranges_valid = all(
    0 <= start < end <= building_faces
    for record in represented
    for start, end in record["output_face_ranges"]
)
represented_geometry_present = all(record.get("output_print_geometry") for record in represented)
general = audit["statistics"].get("building_generalization", {})
represented_count_matches = not records or len(represented) == general["represented_source_count"]
digest = hashlib.file_digest(args.model.open("rb"), "sha256").hexdigest()
report = {
    "model_sha256": digest,
    "audit_hash_matches": digest == audit["model_sha256"],
    "missing_complete_layers": sorted(required - set(layers)),
    "meshes": checks,
    "serialized_support_passed": support.printable,
    "source_output_ranges_valid": source_ranges_valid,
    "represented_source_geometry_present": represented_geometry_present,
    "represented_source_count_matches": represented_count_matches,
    "support_issues": [
        {"code": issue.code, "layer": issue.layer, "message": issue.message}
        for issue in support.issues
    ],
}
if args.previous:
    previous = trimesh.load_scene(args.previous, process=False)
    report["previous_geometry_identical"] = {
        names[name]: bool(
            np.array_equal(mesh.vertices, previous.geometry[name].vertices)
            and np.array_equal(mesh.faces, previous.geometry[name].faces)
        )
        for name, mesh in scene.geometry.items()
        if name in previous.geometry
    }
report["passed"] = (
    report["audit_hash_matches"]
    and support.printable
    and source_ranges_valid
    and represented_geometry_present
    and represented_count_matches
    and not report["missing_complete_layers"]
    and set(scene.geometry) == set(preview.geometry)
    and all(all(row.values()) for row in checks.values())
)
args.report.write_text(json.dumps(report, indent=2))
print(
    json.dumps(
        {key: value for key, value in report.items() if key not in {"meshes", "support_issues"}}
    )
)
assert report["passed"], "Serialized candidate validation failed"
