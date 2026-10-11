"""Explicit, reproducible packaged-generation validation from a saved manifest."""

import json
import time
import traceback
from pathlib import Path


def run(manifest_path):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    report_path = Path(manifest["report_path"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    report = {"passed": False}

    def progress(percent, message):
        report.update(
            percent=percent, message=message, elapsed_seconds=time.perf_counter() - started
        )
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    try:
        from ..generation import GenerationRequest, generate_memory_map
        from ..gpx_loader import load_route_from_gpx
        from ..map_frame import MapFrame
        from .preview import export_preview_glb

        request = dict(manifest["request"])
        request["route"] = load_route_from_gpx(request.pop("gpx_path"))
        request["frame"] = MapFrame(**request["frame"])
        if "parks_file" in request:
            from shapely.geometry import shape
            from ..buildings import _transform_shapely_polygon

            frame = request["frame"]
            request["open_space_polygons"] = [
                _transform_shapely_polygon(
                    shape(f["geometry"]),
                    center_lat=frame.center_lat,
                    center_lon=frame.center_lon,
                    transform={"map_frame": frame},
                )
                for f in json.loads(Path(request.pop("parks_file")).read_text())["features"]
            ]
        result = generate_memory_map(GenerationRequest(**request), progress_callback=progress)
        export_preview_glb(result.meshes, Path(request["output_path"]).with_suffix(".glb"))
        report.update(
            passed=True,
            statistics=result.stats,
            warnings=result.warnings,
            elapsed_seconds=time.perf_counter() - started,
        )
    except Exception as error:
        from ..build_identity import build_identity

        report["error"] = traceback.format_exc()
        report["layer_states"] = getattr(error, "layer_states", {})
        report["processing_statistics"] = getattr(error, "processing_stats", {})
        report["build"] = build_identity()
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1
