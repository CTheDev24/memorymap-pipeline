"""Verify approved frozen inputs and serialized LA candidate after generation."""

import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memorymap_pipeline.printability import audit_printability


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


root = Path("benchmarks/outputs/city-relief")
snapshot = json.loads((root / "approved-input-snapshot.json").read_text())
changed = []
for row in snapshot["files"]:
    path = Path(row["path"])
    if not path.exists() or path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
        changed.append(str(path))
report = {"frozen_files_checked": len(snapshot["files"]), "changed_inputs": changed}
known_paths = {Path(row["path"]) for row in snapshot["files"]}
cache_dirs = {path.parent for path in known_paths if path.parent.name == "building-cache-v1"}
report["cache_files_added"] = sorted(
    str(path) for folder in cache_dirs for path in folder.glob("*.json") if path not in known_paths
)
assert not changed, f"Frozen inputs changed: {changed}"

metrics = json.loads((root / "packaged-full-report.json").read_text())
assert metrics["passed"]
download = metrics["statistics"]["building_generalization"]["download"]
assert download["status"] == "complete" and download["downloaded_tiles"] == 0
assert download["cached_tiles"] == download["tile_count"]
model_hash = digest(root / "packaged-full/la.3mf")
with (root / "packaged-full/la.audit.json").open(encoding="utf-8") as stream:
    audit_hash = re.search(r'"model_sha256": "([a-f0-9]{64})"', stream.read(32768))[1]
assert model_hash == audit_hash
scene = trimesh.load_scene(root / "packaged-full/la.3mf", process=False)
preview = trimesh.load_scene(root / "packaged-full/la.glb", process=False)
assert set(scene.geometry) == set(preview.geometry)
layers = {}
checks = {}
for name, mesh in scene.geometry.items():
    layer = {
        "Base_White": "base",
        "Route_Accent": "route",
        "Roads_Black": "roads",
        "Buildings_Verification": "buildings",
        "Start_Marker": "start_marker",
        "Finish_Marker": "finish_marker",
    }[name]
    layers[layer] = mesh
    reference = metrics["statistics"]["layers"][layer]
    display = preview.geometry[name]
    same_faces = np.array_equal(mesh.faces, display.faces)
    same_vertices = np.array_equal(np.asarray(mesh.vertices, dtype=np.float32), display.vertices)
    checks[layer] = {
        "vertices": len(mesh.vertices),
        "faces": len(mesh.faces),
        "matches_audited_counts": len(mesh.faces) == reference["faces"]
        and len(mesh.vertices) == reference["vertices"],
        "watertight": bool(mesh.is_watertight),
        "consistently_wound": bool(mesh.is_winding_consistent),
        "finite_vertices": bool(np.isfinite(mesh.vertices).all()),
        "positive_volume": bool(mesh.volume > 0),
        "preview_faces_identical": same_faces,
        "preview_vertices_identical_at_glb_float32_precision": same_vertices,
        "bounds_mm": mesh.bounds.tolist(),
    }
    print(layer, checks[layer], flush=True)

missing_layers = sorted(
    {"base", "route", "roads", "buildings", "start_marker", "finish_marker"} - set(layers)
)
support = audit_printability(layers)
report.update(
    model_sha256=model_hash,
    matching_audit_sha256=True,
    meshes=checks,
    missing_requested_layers=missing_layers,
    serialized_support_passed=support.printable,
    support_issues=[
        {"code": i.code, "layer": i.layer, "message": i.message} for i in support.issues
    ],
    download=download,
)
report["passed"] = (
    not missing_layers
    and support.printable
    and all(
        all(
            row[key]
            for key in (
                "matches_audited_counts",
                "watertight",
                "consistently_wound",
                "finite_vertices",
                "positive_volume",
                "preview_faces_identical",
                "preview_vertices_identical_at_glb_float32_precision",
            )
        )
        for row in checks.values()
    )
)
(root / "serialized-export-verification.json").write_text(json.dumps(report, indent=2))
assert report["passed"], "Serialized export verification failed; inspect its report"
print("Serialized export and frozen input verification passed", flush=True)
