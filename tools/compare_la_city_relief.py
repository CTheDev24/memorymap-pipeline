"""Generate and render three frozen LA areas at the full-map print scale."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shapely.geometry import LineString, mapping, shape

from memorymap_pipeline.building_download import download_tiles
from memorymap_pipeline.buildings import (
    OVERPASS_ENDPOINTS,
    _overpass_geometry,
    _transform_shapely_polygon,
)
from memorymap_pipeline.desktop.preview import export_preview_glb
from memorymap_pipeline.generation import GenerationRequest, _query_bounds, generate_memory_map
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame


def render(meshes, out, frame):
    import subprocess

    subprocess.run(
        [sys.executable, "tools/render_city_relief_preview.py", str(out / "sample.glb")], check=True
    )


parser = argparse.ArgumentParser()
parser.add_argument("--area")
parser.add_argument("--mode")
parser.add_argument("--acquire", action="store_true")
parser.add_argument("--line-width", type=float)
args = parser.parse_args()
root = Path("benchmarks/outputs/city-relief")
fixtures = root / "fixtures"
manifest = json.loads((fixtures / "manifest.json").read_text())
audit = json.loads(Path(r"C:/Users/craig/Downloads/la_test.audit.json").read_text())
route = load_route_from_gpx(
    r"C:/Users/craig/iCloudDrive/Business_/Teklo Studio/Product Development/Trace/GPX/LA Marathon.gpx"
)
areas = [args.area] if args.area else ["downtown", "neighborhood", "landmark"]
for area in areas:
    frame = MapFrame(**manifest[area]["frame"])
    bbox = _query_bounds(frame)[0]
    parks_file = fixtures / f"{area}-parks.geojson"
    water_file = fixtures / f"{area}-water.geojson"
    if args.acquire:
        for target, tags in [
            (parks_file, (("leisure", "^(park|garden|nature_reserve)$"),)),
            (water_file, (("natural", "^(water|coastline)$"), "waterway")),
        ]:
            rows = []
            for e in download_tiles(bbox, OVERPASS_ENDPOINTS, tags=tags):
                geometry = _overpass_geometry(e)
                if e["type"] == "way" and e.get("tags", {}).get("waterway") not in {
                    None,
                    "riverbank",
                }:
                    coords = e.get("geometry", [])
                    if len(coords) > 1:
                        geometry = LineString([(c["lon"], c["lat"]) for c in coords])
                if geometry is not None:
                    rows.append(
                        {
                            "type": "Feature",
                            "properties": e.get("tags", {}),
                            "geometry": mapping(geometry),
                        }
                    )
            target.write_text(json.dumps({"type": "FeatureCollection", "features": rows}))
    if not parks_file.exists() or not water_file.exists():
        raise RuntimeError("Run --acquire once for verified barriers")
    parks = [
        _transform_shapely_polygon(
            shape(f["geometry"]),
            center_lat=frame.center_lat,
            center_lon=frame.center_lon,
            transform={"map_frame": frame},
        )
        for f in json.loads(parks_file.read_text())["features"]
    ]
    for mode in [args.mode] if args.mode else ["existing", "balanced", "dense"]:
        out = root / "prototypes" / area / mode
        out.mkdir(parents=True, exist_ok=True)
        settings = dict(audit["settings"])
        settings.update(route_markers="none")
        if args.line_width:
            settings["line_width_mm"] = args.line_width
        if mode != "existing":
            settings.update(building_appearance_mode="city_relief", city_relief_detail=mode)
        if mode == "existing" and (out / "sample.glb").exists() and not args.line_width:
            import trimesh

            scene = trimesh.load(out / "sample.glb", force="scene")
            render(scene.geometry, out, frame)
            continue
        t = time.perf_counter()
        result = generate_memory_map(
            GenerationRequest(
                route=route,
                frame=frame,
                config=settings,
                output_path=out / "sample.3mf",
                roads_file=fixtures / f"{area}-roads.geojson",
                buildings_file=fixtures / f"{area}-buildings.geojson",
                water_file=water_file,
                open_space_polygons=parks,
            ),
            progress_callback=lambda p, t, area=area, mode=mode: print(
                area, mode, p, t, flush=True
            ),
        )
        export_preview_glb(result.meshes, out / "sample.glb")
        (out / "metrics.json").write_text(
            json.dumps(
                {
                    "elapsed_seconds": time.perf_counter() - t,
                    "statistics": result.stats,
                    "warnings": result.warnings,
                },
                indent=2,
            )
        )
        render(result.meshes, out, frame)
