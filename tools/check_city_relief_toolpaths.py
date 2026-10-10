"""Measure above-base extrusion centerlines inside audited building footprints.

This is toolpath presence evidence, not a physical strength or gap acceptance test.
"""

import json
import re
import zipfile
from collections import Counter
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

root = Path("benchmarks/outputs/city-relief/slicer")
reports = []
for area in ("downtown", "neighborhood", "landmark"):
    audit = json.loads((root / f"{area}-final-source.audit.json").read_text())
    result = json.loads((root / f"{area}-final-result.json").read_text(encoding="utf-8-sig"))
    assert audit["model_sha256"] == result["source_sha256"]
    groups = {}
    for record in audit["buildings"]:
        if record.get("output_print_geometry") and record.get("output_face_ranges"):
            key = record.get("group_id", record["id"])
            groups.setdefault(key, (shape(record["output_print_geometry"]), record["disposition"]))
    keys = list(groups)
    polygons = [groups[key][0] for key in keys]
    tree = STRtree(polygons)
    with zipfile.ZipFile(root / f"{area}-final-sliced.3mf") as archive:
        plate = json.loads(archive.read("Metadata/plate_1.json"))
        offset_x, offset_y = plate["bbox_all"][:2]
        gcode = archive.read("Metadata/plate_1.gcode").decode()
    x = y = z = 0.0
    relative_extrusion = False
    previous_e = 0.0
    touched = set()
    segments = 0
    for line in gcode.splitlines():
        line = line.split(";", 1)[0].strip()
        if line == "M83":
            relative_extrusion = True
        elif line == "M82":
            relative_extrusion = False
        elif line.startswith("G92 E"):
            previous_e = float(line[5:])
        elif line.startswith(("G0 ", "G1 ")):
            fields = {
                key: float(value)
                for key, value in re.findall(r"([XYZE])([-+]?(?:\d+\.?\d*|\.\d+))", line)
            }
            nx, ny, nz = fields.get("X", x), fields.get("Y", y), fields.get("Z", z)
            e = fields.get("E", 0 if relative_extrusion else previous_e)
            delta_e = e if relative_extrusion else e - previous_e
            if "E" in fields:
                previous_e = e
            if delta_e > 0 and nz > 1.6 + 0.05 and (nx != x or ny != y):
                segments += 1
                point = Point((x + nx) / 2 - offset_x, (y + ny) / 2 - offset_y)
                for index in tree.query(point, predicate="intersects"):
                    touched.add(keys[int(index)])
            x, y, z = nx, ny, nz
    total = Counter(value[1] for value in groups.values())
    found = Counter(groups[key][1] for key in touched)
    report = {
        "area": area,
        "source_sha256": audit["model_sha256"],
        "method": "positive-extrusion G1 midpoint within audited footprint above base+0.05mm",
        "limitations": "Presence only; not proof of full silhouette, gaps, strength or source-by-source preservation.",
        "output_forms": len(groups),
        "forms_with_toolpaths": len(touched),
        "disposition_totals": dict(total),
        "disposition_with_toolpaths": dict(found),
        "forms_without_toolpaths": sorted(set(keys) - touched),
        "checked_segments": segments,
        "seconds": result["sliced_plates"][0]["total_predication"],
        "grams": result["sliced_plates"][0]["filaments"][0]["total_used_g"],
    }
    reports.append(report)
    print(area, len(touched), "/", len(groups), "forms have above-base toolpaths", dict(found))
(root / "toolpath-survival.json").write_text(json.dumps(reports, indent=2))
