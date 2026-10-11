"""Render actual Bambu above-base urban road paths for visual inspection.

Plots positive-extrusion centerlines; line width, strength and full 3D silhouette
are not inferred. Road class ownership is drawn from the imported geometry.
"""
import argparse
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from itertools import pairwise
from pathlib import Path

import matplotlib
from check_map_style_slicer import NS, PRODUCTION, transform, vertices
from gcode_centerlines import arc_midpoints
from shapely.geometry import Polygon
from shapely.ops import unary_union

matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.collections import LineCollection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slicer", type=Path)
    args = parser.parse_args()
    for style in ("cityscape", "street_grid"):
        folder = args.slicer / style
        with zipfile.ZipFile(folder / "sliced.3mf") as archive:
            model = ET.fromstring(archive.read("3D/3dmodel.model"))
            rotate, translate = transform(model.find("m:build/m:item", NS).attrib.get("transform"))
            components = model.findall("m:resources/m:object/m:components/m:component", NS)
            plate = json.loads(archive.read("Metadata/plate_1.json"))
            registration = json.loads((args.slicer / "registration.json").read_text())[style]
            footprints = {}
            tops = {}
            for component, record in zip(components, registration["parts"]):
                name = record["source_name"]
                if not name.startswith("Roads_") and not name.startswith("Route"):
                    continue
                nested = ET.fromstring(archive.read(component.attrib[PRODUCTION + "path"].lstrip("/")))
                obj = next(obj for obj in nested.findall("m:resources/m:object", NS) if obj.attrib["id"] == component.attrib["objectid"])
                r, t = transform(component.attrib.get("transform"))
                points = (vertices(obj) @ r + t) @ rotate + translate
                faces = [[int(face.attrib[key]) for key in ("v1", "v2", "v3")]
                         for face in obj.findall("m:mesh/m:triangles/m:triangle", NS)]
                triangles = [Polygon(points[face, :2]) for face in faces]
                footprints[name] = unary_union([poly for poly in triangles if poly.area > 1e-10])
                tops[name] = float(points[:, 2].max())
            road_top = min(value for name, value in tops.items() if name.startswith("Roads_"))
            gcode = archive.read("Metadata/plate_1.gcode").decode()
        x = y = z = previous_e = 0.
        relative_e = relative_xyz = False
        segments = []
        levels = set()
        for raw in gcode.splitlines():
            line = raw.split(";", 1)[0].strip()
            if line in {"M82", "M83"}:
                relative_e = line == "M83"
            elif line in {"G90", "G91"}:
                relative_xyz = line == "G91"
            elif line.startswith("G92 E"):
                previous_e = float(line[5:])
            elif line.startswith(("G0 ", "G1 ", "G2 ", "G3 ")):
                f = {key: float(value) for key, value in re.findall(r"([XYZEIJRP])([-+]?(?:\d+\.?\d*|\.\d+))", line)}
                nx, ny, nz = f.get("X", x), f.get("Y", y), f.get("Z", z)
                if relative_xyz:
                    nx, ny, nz = x + f.get("X", 0), y + f.get("Y", 0), z + f.get("Z", 0)
                e = f.get("E", 0 if relative_e else previous_e)
                delta = e if relative_e else e - previous_e
                if "E" in f:
                    previous_e = e
                # Exclude the final base skin at Z1.64 in this 0.16 mm profile.
                if delta > 0 and 1.6 + .08 <= z <= road_top + .01 and (nx != x or ny != y):
                    levels.add(z)
                    if line.startswith(("G2 ", "G3 ")):
                        if "R" in f or f.get("P", 0):
                            raise ValueError("Unsupported extruding arc")
                        points = [(x, y)] + [(a, b) for a, b, _ in arc_midpoints(x, y, nx, ny, f.get("I", 0), f.get("J", 0), clockwise=line.startswith("G2 "))] + [(nx, ny)]
                        segments.extend(pairwise(points))
                    else:
                        segments.append(((x, y), (nx, ny)))
                x, y, z = nx, ny, nz
        fig, axes = plt.subplots(1, 2, figsize=(16, 7))
        colors = ["#377eb8", "#4daf4a", "#984ea3", "#a65628", "#ef6c25"]
        for name, color in zip(footprints, colors):
            region = footprints[name]
            for poly in ([region] if region.geom_type == "Polygon" else region.geoms):
                axes[0].fill(*poly.exterior.xy, color=color, alpha=.65)
                for ring in poly.interiors:
                    axes[0].fill(*ring.xy, color="white")
            axes[0].plot([], [], color=color, label=name)
        axes[0].legend(fontsize=7)
        axes[1].add_collection(LineCollection(segments, colors="#222222", linewidths=.35))
        bounds = plate["bbox_all"]
        for ax, title in zip(axes, ("Imported class footprints", "Actual centerlines above base through road top")):
            ax.set(xlim=(bounds[0], bounds[2]), ylim=(bounds[1], bounds[3]), title=title)
            ax.set_aspect("equal")
        fig.savefig(folder / "road-toolpaths.png", dpi=200)
        plt.close(fig)
        (folder / "toolpath-plot.json").write_text(json.dumps({"levels_mm": sorted(levels), "segments": len(segments), "road_top_mm": road_top,
                                                           "limitations": "Above-base layers through road top only; presence does not prove continuity, full width, silhouette or physical reliability."}, indent=2))


if __name__ == "__main__":
    main()
