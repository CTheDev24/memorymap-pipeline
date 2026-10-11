"""Measure above-base extrusion centerlines inside audited building footprints.

This is toolpath presence evidence, not a physical strength or gap acceptance test.
"""

import argparse
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

from gcode_centerlines import arc_midpoints
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, default=Path("benchmarks/outputs/city-relief/slicer"))
args = parser.parse_args()
root = args.root
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
    relative_position = False
    previous_e = 0.0
    touched = set()
    segments = 0
    for line in gcode.splitlines():
        line = line.split(";", 1)[0].strip()
        if line == "M83":
            relative_extrusion = True
        elif line == "M82":
            relative_extrusion = False
        elif line == "G91":
            relative_position = True
        elif line == "G90":
            relative_position = False
        elif line in ("G90.1", "G18", "G19"):
            raise ValueError(f"Unsupported arc coordinate convention: {line}")
        elif line.startswith("G92 E"):
            previous_e = float(line[5:])
        elif line.startswith(("G0 ", "G1 ", "G2 ", "G3 ")):
            fields = {
                key: float(value)
                for key, value in re.findall(r"([XYZEIJRP])([-+]?(?:\d+\.?\d*|\.\d+))", line)
            }
            nx, ny, nz = fields.get("X", x), fields.get("Y", y), fields.get("Z", z)
            if relative_position:
                nx, ny, nz = x + fields.get("X", 0), y + fields.get("Y", 0), z + fields.get("Z", 0)
            e = fields.get("E", 0 if relative_extrusion else previous_e)
            delta_e = e if relative_extrusion else e - previous_e
            if "E" in fields:
                previous_e = e
            arc = line.startswith(("G2 ", "G3 "))
            if delta_e > 0 and max(z, nz) > 1.6 + 0.05 and (arc or nx != x or ny != y):
                segments += 1
                if arc:
                    if "R" in fields or fields.get("P", 0):
                        raise ValueError("Unsupported extruding R/P arc")
                    samples = arc_midpoints(
                        x,
                        y,
                        nx,
                        ny,
                        fields.get("I", 0),
                        fields.get("J", 0),
                        clockwise=line.startswith("G2 "),
                    )
                else:
                    samples = [((x + nx) / 2, (y + ny) / 2, 0.5)]
                for px, py, fraction in samples:
                    if z + (nz - z) * fraction <= 1.6 + 0.05:
                        continue
                    point = Point(px - offset_x, py - offset_y)
                    for index in tree.query(point, predicate="intersects"):
                        touched.add(keys[int(index)])
            x, y, z = nx, ny, nz
    total = Counter(value[1] for value in groups.values())
    found = Counter(groups[key][1] for key in touched)
    report = {
        "area": area,
        "source_sha256": audit["model_sha256"],
        "method": "positive-extrusion G1 midpoint and G2/G3 relative-I/J centerline samples every <=0.05mm within audited footprint above base+0.05mm",
        "limitations": "Sampled presence only; unmatched forms are not proof of absence. Not proof of full silhouette, gaps, strength or source-by-source preservation.",
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
