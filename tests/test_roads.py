from __future__ import annotations

import json

from shapely.geometry import Point

from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.roads import download_and_build_roads


def test_major_highways_mark_their_mesh_for_terrain_smoothing(tmp_path) -> None:
    roads_file = tmp_path / "major-road.geojson"
    roads_file.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"highway": "motorway"},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [-95.371, 29.760],
                                [-95.369, 29.760],
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    frame = MapFrame(
        center_lat=29.760,
        center_lon=-95.370,
        coverage_width_m=300.0,
        coverage_height_m=200.0,
        print_width_mm=120.0,
        print_height_mm=90.0,
        margin_mm=5.0,
    )

    _roads, mesh = download_and_build_roads(
        bbox=None,
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        road_types=["motorway"],
        road_widths={"motorway": 2.4},
        road_height_mm=0.8,
        map_width_mm=frame.print_width_mm,
        map_height_mm=frame.print_height_mm,
        margin_mm=frame.margin_mm,
        roads_file=str(roads_file),
        terrain_smoothing_types=["motorway"],
    )

    assert mesh is not None
    region = mesh.metadata.get("terrain_smoothing_region")
    assert region is not None
    assert region.covers(Point(frame.print_width_mm / 2.0, frame.print_height_mm / 2.0))
    corridors = mesh.metadata.get("terrain_profile_corridors")
    assert corridors is not None
    assert len(corridors) == 1
    assert corridors[0].classification == "motorway"
    assert corridors[0].width_mm == 2.4
    assert corridors[0].region.covers(
        Point(frame.print_width_mm / 2.0, frame.print_height_mm / 2.0)
    )
