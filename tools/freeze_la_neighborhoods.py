"""Extract frozen OSM building and street fixtures at the LA print scale."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shapely.geometry import LineString, box, mapping

from memorymap_pipeline.buildings import _overpass_geometry
from memorymap_pipeline.generation import _query_bounds
from memorymap_pipeline.map_frame import MapFrame

out = Path("benchmarks/outputs/city-relief/fixtures")
out.mkdir(parents=True, exist_ok=True)
audit = json.loads(Path(r"C:/Users/craig/Downloads/la_test.audit.json").read_text())
scale = audit["print_scale"]["meters_per_mm"]
regions = {
    name: MapFrame(lat, lon, 24 * scale, 24 * scale, 24, 24, 0)
    for name, lat, lon in [
        ("downtown", 34.046, -118.258),
        ("neighborhood", 34.082, -118.318),
        ("landmark", 34.056, -118.249),
    ]
}
full = MapFrame(**audit["frame"])
regions["full"] = full
boxes = {
    name: box(b[2], b[0], b[3], b[1])
    for name, frame in regions.items()
    for b in [_query_bounds(frame)[0]]
}
features = {name: [] for name in regions if name != "full"}
seen = set()
landmarks = []
for path in sorted(
    (Path(os.environ["LOCALAPPDATA"]) / "Trace Studio/building-cache-v1").glob("*.json")
):
    for e in json.loads(path.read_text())["elements"]:
        if not any(tag in e.get("tags", {}) for tag in ("building", "building:part")):
            continue
        key = (e["type"], e["id"])
        if key in seen:
            continue
        seen.add(key)
        coords = e.get("geometry", []) or [
            c for member in e.get("members", []) for c in member.get("geometry", [])
        ]
        if not coords:
            continue
        bounds = box(
            min(c["lon"] for c in coords),
            min(c["lat"] for c in coords),
            max(c["lon"] for c in coords),
            max(c["lat"] for c in coords),
        )
        selected = [
            name for name, region in boxes.items() if name != "full" and region.intersects(bounds)
        ]
        if not selected:
            continue
        polygon = _overpass_geometry(e)
        if polygon is None:
            continue
        tags = {**e.get("tags", {}), "_osm_type": e["type"], "_osm_id": e["id"]}
        feature = {"type": "Feature", "properties": tags, "geometry": mapping(polygon)}
        for name in selected:
            features[name].append(feature)
    print("building cache", path.name, flush=True)
for name, rows in features.items():
    (out / f"{name}-buildings.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": rows})
    )
roads_path = (
    Path(os.environ["LOCALAPPDATA"])
    / "MemoryMap/cache/a8482b0649dc17a2fcf743ff1282b3255bb28bff.json"
)
data = json.loads(roads_path.read_text())
nodes = {e["id"]: (e["lon"], e["lat"]) for e in data["elements"] if e["type"] == "node"}
roads = {name: [] for name in regions}
for e in data["elements"]:
    if e["type"] != "way" or "highway" not in e.get("tags", {}):
        continue
    coords = [nodes[n] for n in e.get("nodes", []) if n in nodes]
    if len(coords) < 2:
        continue
    line = LineString(coords)
    feature = {
        "type": "Feature",
        "properties": {**e["tags"], "_osm_id": e["id"]},
        "geometry": mapping(line),
    }
    for name, region in boxes.items():
        if line.intersects(region):
            roads[name].append(feature)
for name, rows in roads.items():
    (out / f"{name}-roads.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": rows})
    )
manifest = {
    name: {
        "frame": frame.__dict__,
        "building_count": len(features.get(name, [])),
        "road_count": len(roads[name]),
    }
    for name, frame in regions.items()
}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2))
print(json.dumps(manifest, indent=2))
