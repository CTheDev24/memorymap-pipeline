"""Check Bambu CLI assembly registration and component metadata.

This does not substitute for interactive filament assignment, toolpath visual
inspection or physical printing. Bambu's import may rename standard 3MF parts.
"""
import argparse
import json
import xml.etree.ElementTree as ET
import zipfile
from hashlib import sha256
from pathlib import Path

import numpy as np

NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
PRODUCTION = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"


def vertices(obj):
    return np.array([[float(v.attrib[key]) for key in ("x", "y", "z")]
                     for v in obj.findall("m:mesh/m:vertices/m:vertex", NS)])


def transform(value):
    numbers = np.array([float(x) for x in value.split()]) if value else np.array([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0])
    return numbers[:9].reshape(3, 3), numbers[9:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("representative", type=Path)
    parser.add_argument("slicer", type=Path)
    args = parser.parse_args()
    reports = {}
    for style in ("cityscape", "terrain", "street_grid"):
        source = args.representative / style / "candidate.3mf"
        audit = json.loads(source.with_suffix(".audit.json").read_text())
        assert sha256(source.read_bytes()).hexdigest() == audit["model_sha256"]
        with zipfile.ZipFile(source) as archive:
            original = ET.fromstring(archive.read("3D/3dmodel.model"))
        original_parts = [obj for obj in original.findall("m:resources/m:object", NS)
                          if obj.find("m:mesh", NS) is not None]
        with zipfile.ZipFile(args.slicer / style / "sliced.3mf") as archive:
            imported = ET.fromstring(archive.read("3D/3dmodel.model"))
            settings = ET.fromstring(archive.read("Metadata/model_settings.config"))
            plate = json.loads(archive.read("Metadata/plate_1.json"))
            project = json.loads(archive.read("Metadata/project_settings.config"))
            build = imported.findall("m:build/m:item", NS)
            assert len(build) == 1
            components = imported.findall("m:resources/m:object/m:components/m:component", NS)
            assert len(components) == len(original_parts)
            parts_report = []
            for source_obj, component, part in zip(original_parts, components, settings.findall("object/part")):
                nested = ET.fromstring(archive.read(component.attrib[PRODUCTION + "path"].lstrip("/")))
                obj = next(obj for obj in nested.findall("m:resources/m:object", NS)
                           if obj.attrib["id"] == component.attrib["objectid"])
                rotation, translation = transform(component.attrib.get("transform"))
                packed = vertices(obj) @ rotation + translation
                expected = vertices(source_obj)
                assert packed.shape == expected.shape
                deviation = float(np.abs(packed - expected).max())
                assert deviation < 2e-5, (style, source_obj.attrib["name"], deviation)
                metadata = {x.attrib["key"]: x.attrib.get("value") for x in part.findall("metadata") if "key" in x.attrib}
                stat = part.find("mesh_stat")
                parts_report.append({"source_name": source_obj.attrib["name"],
                                     "bambu_name": metadata.get("name"),
                                     "max_registration_error_mm": deviation,
                                     "mesh_repairs": dict(stat.attrib) if stat is not None else {}})
            bbox = plate["bbox_all"]
            dimensions = [bbox[2] - bbox[0], bbox[3] - bbox[1]]
            assert np.allclose(sorted(dimensions), [190, 240], atol=.001)
            reports[style] = {"source_sha256": audit["model_sha256"], "one_registered_assembly": True,
                              "dimensions_mm": dimensions,
                              "whole_assembly_rotation_allowed": True,
                              "parts": parts_report, "outer_wall_line_width_mm": project.get("outer_wall_line_width"),
                              "filaments_used": plate["filament_ids"],
                              "shared_filament_cli_slice": len(plate["filament_ids"]) == 1,
                              "gui_independent_assignment": "pending",
                              "visual_toolpath_inspection": "pending",
                              "physical_qualification": "pending"}
    (args.slicer / "registration.json").write_text(json.dumps(reports, indent=2))
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
