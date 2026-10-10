"""Verify packaged/source geometry payloads and each export's audit hash."""

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memorymap_pipeline.build_identity import build_identity

root = Path("benchmarks/outputs/city-relief")
namespace = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


def geometry(path, expected_build=None):
    audit = json.loads(path.with_suffix(".audit.json").read_text())
    if expected_build is not None:
        assert audit["build"]["source_sha256"] == expected_build, f"Stale build: {path}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == audit["model_sha256"]
    with zipfile.ZipFile(path) as archive:
        model = ET.fromstring(archive.read("3D/3dmodel.model"))
    return [
        {
            "name": obj.get("name"),
            "vertices": [v.attrib for v in obj.findall(".//m:vertex", namespace)],
            "triangles": [t.attrib for t in obj.findall(".//m:triangle", namespace)],
            "components": [c.attrib for c in obj.findall(".//m:component", namespace)],
        }
        for obj in model.findall(".//m:object", namespace)
        if obj.find("m:mesh", namespace) is not None
    ]


reports = []
fingerprint = build_identity()["source_sha256"]
for area in ("downtown", "neighborhood", "landmark"):
    packaged = root / "packaged-prototypes" / area / "sample.3mf"
    source = root / "prototypes" / area / "balanced" / "sample.3mf"
    matches = geometry(packaged, fingerprint) == geometry(source, fingerprint)
    slice_matches = geometry(root / "slicer" / f"{area}-final-source.3mf") == geometry(
        source, fingerprint
    )
    existing_matches = geometry(
        root / "prototypes" / area / "existing" / "sample.3mf", fingerprint
    ) == geometry(root / "pre-local-overlay" / area / "existing" / "sample.3mf")
    reports.append(
        {
            "area": area,
            "model_payload_identical": matches,
            "slicer_source_geometry_identical": slice_matches,
            "existing_mode_compatible": existing_matches,
            "audit_hashes_valid": True,
            "current_build_verified": True,
        }
    )
    print(area, "model payload identical:", matches)
    assert matches
    assert slice_matches
    assert existing_matches
(root / "package-output-verification.json").write_text(json.dumps(reports, indent=2))
