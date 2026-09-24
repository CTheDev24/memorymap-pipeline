"""Source-backed Peachtree regression run; preserves GPX and freezes OSM inputs."""

import argparse
import cProfile
import hashlib
import itertools
import json
import logging
import math
import os
import pstats
import shutil
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("gpx", type=Path)
    parser.add_argument("--snapshot", type=Path, help="Reuse verified frozen inputs; never download")
    parser.add_argument("--profile", action="store_true", help="Profile generation including export")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "benchmarks/outputs/peachtree-gpx-performance"
    )
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.snapshot:
        snapshot = args.snapshot.resolve()
        for name in ("inputs.json", "roads.geojson", "water.geojson", "buildings.geojson"):
            destination = out / name
            if not destination.exists():
                shutil.copyfile(snapshot / name, destination)
    os.environ["LOCALAPPDATA"] = str(out / "appdata")
    os.environ["MPLCONFIGDIR"] = str(out / "mpl-cache")
    logging.basicConfig(
        filename=out / "generation.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    import osmnx as ox

    from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
    from memorymap_pipeline.gpx_loader import load_route_from_gpx
    from memorymap_pipeline.map_frame import MapFrame
    from memorymap_pipeline.water import WATER_TAGS

    target = out / "route.gpx"
    if target.exists():
        assert target.read_bytes() == args.gpx.read_bytes(), (
            "Output directory belongs to a different GPX"
        )
    else:
        shutil.copyfile(args.gpx, target)
    route = load_route_from_gpx(target)
    frame = MapFrame.fit_route(route.points, 190, 240, margin_mm=0, route_padding_mm=5.6)
    config = {
        "building_generalization_mode": "print_optimized",
        "line_width_mm": 0.42,
        "max_print_height_mm": 26,
        "terrain_enabled": False,
        "water_enabled": False,
        "flat_border_enabled": False,
        "route_markers": "both",
        "style_profile": "urban",
    }
    radius = math.hypot(frame.coverage_width_m, frame.coverage_height_m) / 2
    ox.settings.cache_folder = out / "osm-cache"
    ox.settings.use_cache = True
    ox.settings.requests_timeout = 180
    ox.settings.log_file = False
    center = (frame.center_lat, frame.center_lon)
    manifest_path = out / "inputs.json"
    manifest = (
        json.loads(manifest_path.read_text())
        if manifest_path.exists()
        else {
            "gpx_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "route_points": len(route.points),
            "frame": asdict(frame),
            "config": config,
            "query_radius_m": radius,
            "sources": {},
            "limitations": [
                "Auto-fitted frame; exact stalled-build frame unavailable",
                "OSM snapshot may differ from original build",
                "No slicing or physical print validation",
            ],
        }
    )
    assert manifest["gpx_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert manifest["frame"] == asdict(frame), "Frozen snapshot frame differs"
    assert manifest["config"] == config, "Frozen snapshot configuration differs"
    print(json.dumps({"frame": asdict(frame), "route_points": len(route.points)}), flush=True)
    for role in ("roads", "water", "buildings"):
        path = out / f"{role}.geojson"
        if path.exists() and role in manifest["sources"]:
            assert (
                hashlib.sha256(path.read_bytes()).hexdigest() == manifest["sources"][role]["sha256"]
            )
            print(f"Reusing frozen {role}", flush=True)
            continue
        if args.snapshot:
            raise ValueError(f"Frozen snapshot is incomplete: {role}")
        print(f"Fetching source {role}", flush=True)
        start = time.perf_counter()
        if role == "roads":
            graph = ox.graph_from_point(center, dist=radius, network_type="all")
            data = ox.graph_to_gdfs(graph, nodes=False, edges=True, fill_edge_geometry=True)
        else:
            tags = WATER_TAGS if role == "water" else {"building": True, "building:part": True}
            data = ox.features_from_point(center, tags=tags, dist=radius)
        path.write_text(data.reset_index().to_json(to_wgs84=True), encoding="utf-8")
        manifest["sources"][role] = {
            "path": path.name,
            "features": len(data),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "acquired_at": datetime.now(UTC).isoformat(),
            "acquisition_seconds": time.perf_counter() - start,
            "origin": "OpenStreetMap via OSMnx / Overpass",
            "license": "ODbL 1.0; OpenStreetMap contributors",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))
        print(f"Frozen {role}: {len(data)} features", flush=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    # Record exactly which sources were benchmarked, including uncommitted edits.
    (out / "implementation.json").write_text(json.dumps({
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "memorymap_pipeline").glob("*.py"))
    }, indent=2))
    records = []
    events = []
    (out / "progress.jsonl").write_text("")
    started = time.perf_counter()

    def progress(percent, message):
        event = {"seconds": time.perf_counter() - started, "percent": percent, "message": message}
        events.append(event)
        print(json.dumps(event), flush=True)
        with (out / "progress.jsonl").open("a") as stream:
            stream.write(json.dumps(event) + "\n")

    # GDAL coerces mixed scalar/list OSM tags into numpy arrays. Decode the
    # frozen features directly so replay has the same native tags as OSMnx.
    import geopandas as gpd

    real_read_file = gpd.read_file
    snapshot_paths = {
        (out / f"{role}.geojson").resolve() for role in ("roads", "water", "buildings")
    }

    def read_snapshot(path, *args, **kwargs):
        if Path(path).resolve() in snapshot_paths:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return gpd.GeoDataFrame.from_features(data["features"], crs="EPSG:4326")
        return real_read_file(path, *args, **kwargs)

    profiler = cProfile.Profile() if args.profile else None
    try:
        if profiler:
            profiler.enable()
        with patch.object(gpd, "read_file", side_effect=read_snapshot):
            result = generate_memory_map(
                GenerationRequest(
                    route=route,
                    frame=frame,
                    output_path=out / "peachtree-fixed.3mf",
                    config=config,
                    roads_file=out / "roads.geojson",
                    water_file=out / "water.geojson",
                    buildings_file=out / "buildings.geojson",
                    building_diagnostics=records,
                ),
                progress_callback=progress,
            )
        if profiler:
            profiler.disable()
        report = {
            "source_replay": "Frozen GeoJSON with native OSM tag types",
            "elapsed_seconds": time.perf_counter() - started,
            "stats": result.stats,
            "warnings": result.warnings,
            "progress_events": len(events),
            "progress_monotonic": all(
                a["percent"] <= b["percent"] for a, b in itertools.pairwise(events)
            ),
            "mesh_checks": {
                name: {
                    "vertices": len(mesh.vertices),
                    "faces": len(mesh.faces),
                    "watertight": bool(mesh.is_watertight),
                    "winding_consistent": bool(mesh.is_winding_consistent),
                }
                for name, mesh in result.meshes.items()
            },
            "model_sha256": hashlib.sha256(result.output_path.read_bytes()).hexdigest(),
            "limitations": manifest["limitations"],
        }
        (out / "result.json").write_text(json.dumps(report, indent=2))
        assert result.roads_mesh is not None and result.buildings_mesh is not None
        assert result.stats["building_grouping"]["enabled"], "Grouping was unexpectedly skipped"
        assert report["progress_monotonic"]
        (out / "failure.json").unlink(missing_ok=True)
        print(
            json.dumps(
                {
                    "completed": str(result.output_path),
                    "elapsed_seconds": report["elapsed_seconds"],
                    "building_generalization": result.stats["building_generalization"],
                }
            ),
            flush=True,
        )
    except Exception as exc:
        (out / "failure.json").write_text(
            json.dumps(
                {"error": repr(exc), "elapsed_seconds": time.perf_counter() - started}, indent=2
            )
        )
        raise
    finally:
        if profiler:
            profiler.disable()
            profiler.dump_stats(str(out / "generation.prof"))
            with (out / "profile.txt").open("w") as stream:
                pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(100)


if __name__ == "__main__":
    main()
