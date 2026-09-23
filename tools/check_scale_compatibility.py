"""Offline geometry fingerprints for comparison with a pre-Phase-1 checkout."""

import hashlib
import json
from pathlib import Path

import numpy as np

from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame


def capture():
    fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    route = load_route_from_gpx(fixtures / "frame_route.gpx")
    result = {}
    for extent in (200, 20000):
        for grouping, filtering in ((False, 0.0), (True, 0.0), (True, 0.8)):
            records = []
            generated = generate_memory_map(
                GenerationRequest(
                    route=route,
                    frame=MapFrame(29.76, -95.37, extent, extent, 190, 240, 5),
                    output_path="unused.3mf",
                    export_model=False,
                    roads_file=fixtures / "frame_roads.geojson",
                    buildings_file=fixtures / "frame_buildings.geojson",
                    water_file=fixtures / "frame_water.geojson",
                    building_diagnostics=records,
                    config={
                        "building_grouping_enabled": grouping,
                        "residential_min_width_mm": filtering,
                    },
                )
            )
            result[f"{extent}-{grouping}-{filtering}"] = {
                "meshes": {
                    name: hashlib.sha256(
                        np.asarray(mesh.vertices).tobytes() + np.asarray(mesh.faces).tobytes()
                    ).hexdigest()
                    for name, mesh in generated.meshes.items()
                },
                "records": [
                    {
                        k: r.get(k)
                        for k in (
                            "id",
                            "status",
                            "reason",
                            "group_id",
                            "group_source_ids",
                            "output_face_ranges",
                        )
                    }
                    for r in records
                ],
                "grouping": generated.stats["building_grouping"],
                "filter": generated.stats["building_filter"],
            }
    return result


if __name__ == "__main__":
    print(json.dumps(capture(), indent=2, sort_keys=True))
