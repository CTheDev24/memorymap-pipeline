"""Compare full frozen-input runs without ZIP timestamps or runtime telemetry."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def stable(value):
    if isinstance(value, dict):
        return {
            k: stable(v) for k, v in value.items()
            if k not in {"stage_seconds", "optimization_seconds", "export_seconds", "model_sha256"}
        }
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def inspect_run(folder):
    model = folder / "peachtree-fixed.3mf"
    audit = json.loads(model.with_suffix(".audit.json").read_text(encoding="utf-8"))
    assert audit["model_sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()
    with zipfile.ZipFile(model) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    ns = {"m": root.tag.split("}")[0][1:]}
    objects = {}
    for obj in root.findall(".//m:object", ns):
        mesh = obj.find("m:mesh", ns)
        if mesh is None:
            continue
        vertices = [v.attrib for v in mesh.findall("m:vertices/m:vertex", ns)]
        faces = [f.attrib for f in mesh.findall("m:triangles/m:triangle", ns)]
        objects[obj.get("name", obj.get("id"))] = {
            "vertices": len(vertices), "faces": len(faces),
            "geometry_sha256": digest([vertices, faces]),
        }
    return {
        "objects": objects,
        "audit_without_runtime_sha256": digest(stable(audit)),
        "source_decisions_sha256": digest(audit["buildings"]),
        "statistics_without_runtime_sha256": digest(stable(audit["statistics"])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before, after = inspect_run(args.before), inspect_run(args.after)
    report = {"matches": before == after, "before": before, "after": after}
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    assert report["matches"], "Geometry, decisions, or non-runtime audit fields changed"


if __name__ == "__main__":
    main()
