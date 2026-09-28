"""Replay the user LA audit through the production generator."""
import json
import logging
import sys
from pathlib import Path
from time import perf_counter
from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    audit = json.loads(Path(r"C:\Users\craig\Downloads\la_marathon.audit.json").read_text())
    output = Path("benchmarks/outputs/la-download-recovery")
    output.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    if "--download-only" in sys.argv:
        from memorymap_pipeline.generation import _query_bounds
        from memorymap_pipeline.building_download import download_tiles
        from memorymap_pipeline.buildings import OVERPASS_ENDPOINTS
        stats = {}
        data = download_tiles(_query_bounds(MapFrame(**audit["frame"]))[0], OVERPASS_ENDPOINTS,
                              progress=lambda p, text: print(text, flush=True), diagnostics=stats)
        stats["elapsed_seconds"] = perf_counter()-started
        (output / "live-download.json").write_text(json.dumps(stats, indent=2))
        print(json.dumps(stats), flush=True)
        sys.exit(0)
    result = generate_memory_map(GenerationRequest(
        route=load_route_from_gpx(r"C:\Users\craig\iCloudDrive\Business_\Teklo Studio\Product Development\Trace\GPX\LA Marathon.gpx"),
        frame=MapFrame(**audit["frame"]), output_path=output / "la-marathon-fixed.3mf",
        config=audit["settings"],
    ), progress_callback=lambda p, text: print(f"{perf_counter()-started:.1f}s {p}% {text}", flush=True))
    print("COMPLETE", result.output_path, flush=True)
