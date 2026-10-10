"""Render the original baseline's actual export, including exporter cleanup."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import trimesh
from memorymap_pipeline.desktop.preview import export_preview_glb

root = Path("benchmarks/outputs/city-relief/existing")
scene = trimesh.load_scene(root / "la.3mf", process=False)
meshes = dict(scene.geometry)
export_preview_glb(meshes, root / "la-exported.glb")
(root / "preview-provenance.json").write_text(
    json.dumps(
        {
            "source": "la.3mf",
            "preview": "la-exported.glb",
            "reason": "Original baseline predates cleaned mesh reconciliation; render exported geometry.",
            "mesh_faces": {name: len(mesh.faces) for name, mesh in meshes.items()},
        },
        indent=2,
    )
)
