"""Generate offline Phase 1 diagnostics: fixture route and synthetic long route.

Run: python -m tools.scale_diagnostics_samples --output DIRECTORY
"""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np
from shapely.geometry import box

from memorymap_pipeline.building_generalization import measure_footprints
from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
from memorymap_pipeline.gpx_loader import Route, RoutePoint, load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.print_scale import PrintScaleContext
from memorymap_pipeline.projection import unproject_local_array


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    compact = load_route_from_gpx(fixtures / "frame_route.gpx")
    lat, lon = unproject_local_array(np.array([[0, -10450], [0, 10450]]), 29.76, -95.37)
    long_route = Route([RoutePoint(a, b) for a, b in zip(lat, lon)])
    with TemporaryDirectory(dir=output) as temp:
        for name, route in [
            ("houston-fixture-compact", compact),
            ("synthetic-linear-20.9km", long_route),
        ]:
            frame = MapFrame.fit_route(route.points, 190, 240, 5, route_padding_mm=0.6)
            buildings = fixtures / "frame_buildings.geojson"
            if name.startswith("synthetic"):
                # Same 10/20/40/73 m footprints repeated beside a 20.9 km route.
                features = []
                for i in range(100):
                    width = (10, 20, 40, 73)[i % 4]
                    x = 100 + (i % 5) * 150
                    y = -9500 + (i // 5) * 950
                    xy = np.array(box(x, y, x + width, y + width).exterior.coords)
                    lat, lon = unproject_local_array(xy, 29.76, -95.37)
                    features.append(
                        {
                            "type": "Feature",
                            "properties": {"building": "house"},
                            "geometry": {"type": "Polygon", "coordinates": [list(zip(lon, lat))]},
                        }
                    )
                buildings = Path(temp) / "synthetic-buildings.geojson"
                buildings.write_text(
                    json.dumps({"type": "FeatureCollection", "features": features})
                )
            result = generate_memory_map(
                GenerationRequest(
                    route=route,
                    frame=frame,
                    output_path=Path(temp) / "unused.3mf",
                    export_model=False,
                    include_roads=False,
                    buildings_file=buildings,
                    config={"flat_border_enabled": True},
                )
            )
            (output / f"{name}.json").write_text(
                json.dumps(
                    {
                        "sample": name,
                        "synthetic": name.startswith("synthetic"),
                        "building_generalization": result.stats["building_generalization"],
                    },
                    indent=2,
                    allow_nan=False,
                )
                + "\n"
            )
    context = PrintScaleContext.from_frame(frame, {})
    shapes = [box(0, 0, 0.1 + (i % 30) * 0.05, 1 + (i % 10) * 0.2) for i in range(10000)]
    start = perf_counter()
    measured = measure_footprints(shapes, context)
    timing = {
        "synthetic_rectangles": len(shapes),
        "elapsed_seconds": perf_counter() - start,
        "note": "Local diagnostic microbenchmark; not a complex OSM performance guarantee.",
        "measured_building_count": measured["measured_building_count"],
    }
    (output / "measurement-performance.json").write_text(json.dumps(timing, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
