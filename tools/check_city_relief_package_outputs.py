"""Verify packaged/source geometry payloads and each export's audit hash."""

import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

root = Path("benchmarks/outputs/city-relief")
namespace = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


def geometry(path):
    audit = json.loads(path.with_suffix(".audit.json").read_text())
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
for area in ("downtown", "neighborhood", "landmark"):
    packaged = root / "packaged-prototypes" / area / "sample.3mf"
    source = root / "prototypes" / area / "balanced" / "sample.3mf"
    matches = geometry(packaged) == geometry(source)
    reports.append({"area": area, "model_payload_identical": matches, "audit_hashes_valid": True})
    print(area, "model payload identical:", matches)
    assert matches
(root / "package-output-verification.json").write_text(json.dumps(reports, indent=2))
