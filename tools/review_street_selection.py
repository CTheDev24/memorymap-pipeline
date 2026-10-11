"""Review adaptive selection on frozen real roads without generating a full mesh.

Endpoint graph diagnostics use exact source segment endpoints, not a routable
graph: geometric crossings without shared endpoints are not noded here.
"""
import argparse
import json
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import matplotlib
import networkx as nx
import numpy as np
from shapely.geometry import LineString, box

matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.collections import LineCollection

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.map_styles import EXPERIMENTAL_WIDTHS, ROAD_CLASSES
from memorymap_pipeline.roads import _normalize_highway_value, _normalized_tag_values
from memorymap_pipeline.street_selection import select_streets


def parts(line):
    return [line] if line.geom_type == "LineString" else list(line.geoms)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--crop-mm", nargs=4, type=float, metavar=("LEFT", "BOTTOM", "RIGHT", "TOP"))
    args = parser.parse_args()
    request = json.loads(args.manifest.read_text())["request"]
    frame = MapFrame(**request["frame"])
    source = Path(request["roads_file"])
    route_file = Path(request["gpx_path"])
    args.output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    clip = box(frame.margin_mm, frame.margin_mm,
               frame.print_width_mm - frame.margin_mm, frame.print_height_mm - frame.margin_mm)
    if args.crop_mm:
        clip = clip.intersection(box(*args.crop_mm))
    route = LineString(frame.transform_points(load_route_from_gpx(route_file).points))
    route_region = route.buffer(request["config"]["route_width"] / 2)
    sources = []
    for feature in json.loads(source.read_text())["features"]:
        tags = feature["properties"]
        highway = _normalize_highway_value(tags.get("highway"))
        if highway not in {kind for kinds in ROAD_CLASSES.values() for kind in kinds}:
            continue
        if highway == "service" and _normalized_tag_values(tags.get("service")) & {"alley", "driveway", "parking_aisle"}:
            continue
        if _normalized_tag_values(tags.get("access")) & {"private", "no"}:
            continue
        sources.append((np.asarray(feature["geometry"]["coordinates"]), highway, tags))
    # Batch the same exact projection instead of constructing a projection per
    # segment. This changes only the diagnostic tool, not generation/selection.
    lengths = [len(coords) for coords, _, _ in sources]
    coordinates = np.concatenate([coords for coords, _, _ in sources])
    projected = frame.transform_lonlat(coordinates[:, 1], coordinates[:, 0])
    records = []
    offset = 0
    for count, (_, highway, tags) in zip(lengths, sources):
        line = LineString(projected[offset:offset + count]).intersection(clip)
        offset += count
        if not line.is_empty and line.length > 0:
            names = _normalized_tag_values(tags.get("name")) - {"nan", "none"}
            records.append((line, highway, ";".join(sorted(names))))
    print(f"Projected {len(records)} clipped records in {perf_counter() - started:.2f}s; selecting", flush=True)
    keep, report = select_streets(records, route_region, EXPERIMENTAL_WIDTHS)
    print(f"Selected in {perf_counter() - started:.2f}s; checking endpoint graph", flush=True)
    graphs = [nx.Graph(), nx.Graph()]
    for index, (line, _, _) in enumerate(records):
        for part in parts(line):
            if part.geom_type != "LineString":
                continue
            edge = (tuple(part.coords[0]), tuple(part.coords[-1]))
            graphs[0].add_edge(*edge)
            if index in keep:
                graphs[1].add_edge(*edge)
    components = {point: i for i, group in enumerate(nx.connected_components(graphs[1])) for point in group}
    losses = []
    for index, (line, highway, name) in enumerate(records):
        if index in keep:
            continue
        for part in parts(line):
            if part.geom_type != "LineString":
                continue
            a, b = tuple(part.coords[0]), tuple(part.coords[-1])
            if a in components and b in components and components[a] != components[b]:
                losses.append({"source_index": index, "highway": highway, "name": name,
                               "length_mm": part.length, "endpoints": [a, b]})
    report.update(source_file=str(source), source_sha256=sha256(source.read_bytes()).hexdigest(),
                  route_sha256=sha256(route_file.read_bytes()).hexdigest(), frame=request["frame"],
                  review_clip_mm=list(clip.bounds),
                  seconds=perf_counter() - started,
                  retained_types=dict(Counter(records[i][1] for i in keep)),
                  endpoint_components_before=nx.number_connected_components(graphs[0]),
                  endpoint_components_after=nx.number_connected_components(graphs[1]),
                  omitted_connectors_between_retained_components=losses,
                  limitations="Exact source endpoint graph only; crossings are not noded. LA frozen roads, not Chicago visual acceptance. No mesh or slicer qualification.")
    (args.output / "report.json").write_text(json.dumps(report, indent=2))
    left, bottom, right, top = clip.bounds
    for name, bounds in (("full", (left, right, bottom, top)),
                         ("detail", (left, min(right, left + 35), bottom, min(top, bottom + 35)))):
        fig, axes = plt.subplots(1, 2, figsize=(16, 7))
        for ax, enabled, title in zip(axes, (set(range(len(records))), keep), ("All source roads", "Balanced adaptive selection")):
            segments = [np.asarray(part.coords) for i in enabled for part in parts(records[i][0]) if part.geom_type == "LineString"]
            ax.add_collection(LineCollection(segments, colors="#777777", linewidths=.4))
            ax.plot(*route.xy, color="#ef6c25", linewidth=1.8)
            ax.set(xlim=bounds[:2], ylim=bounds[2:], title=title)
            ax.set_aspect("equal")
        fig.savefig(args.output / f"{name}.png", dpi=180)
        plt.close(fig)
    print(json.dumps({key: report[key] for key in ("source_count", "retained_count", "omitted_count", "seconds", "endpoint_components_before", "endpoint_components_after")}))


if __name__ == "__main__":
    main()
