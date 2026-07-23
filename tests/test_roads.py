from __future__ import annotations

import json

import numpy as np
from shapely.geometry import Point
from trimesh import Trimesh

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


def test_road_layer_keeps_valid_parts_when_one_polygon_extrusion_fails(
    tmp_path, monkeypatch
) -> None:
    roads_file = tmp_path / "roads.geojson"
    roads_file.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"highway": "residential"},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [-95.371, 29.760],
                                [-95.369, 29.760],
                            ],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {"highway": "residential"},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [-95.371, 29.761],
                                [-95.369, 29.761],
                            ],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    frame = MapFrame(
        center_lat=29.7605,
        center_lon=-95.370,
        coverage_width_m=400.0,
        coverage_height_m=300.0,
        print_width_mm=120.0,
        print_height_mm=90.0,
        margin_mm=5.0,
    )

    calls = {"count": 0}

    def _fake_extrude(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("degenerate polygon")
        return Trimesh(
            vertices=np.array(
                [
                    [0.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0],
                    [1.0, 1.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0, 1.0],
                    [1.0, 0.0, 1.0],
                    [1.0, 1.0, 1.0],
                    [0.0, 1.0, 1.0],
                ],
            ),
            faces=np.array(
                [
                    [0, 2, 1],
                    [0, 3, 2],
                    [4, 5, 6],
                    [4, 6, 7],
                    [0, 1, 5],
                    [0, 5, 4],
                    [1, 2, 6],
                    [1, 6, 5],
                    [2, 3, 7],
                    [2, 7, 6],
                    [3, 0, 4],
                    [3, 4, 7],
                ]
            ),
            process=False,
        )

    monkeypatch.setattr("memorymap_pipeline.roads.route_mesh_from_polygon", _fake_extrude)

    _roads, mesh = download_and_build_roads(
        bbox=None,
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        road_types=["residential"],
        road_widths={"residential": 1.1},
        road_height_mm=0.8,
        map_width_mm=frame.print_width_mm,
        map_height_mm=frame.print_height_mm,
        margin_mm=frame.margin_mm,
        roads_file=str(roads_file),
    )

    assert calls["count"] >= 2
    assert mesh is not None
    assert len(mesh.faces) > 0
