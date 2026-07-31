from __future__ import annotations

import json

import numpy as np
from shapely.geometry import LineString, Point, box
from trimesh import Trimesh

from memorymap_pipeline.config import DEFAULT_CONFIG
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.roads import _extrude_road_polygon, download_and_build_roads


def test_urban_defaults_keep_cycleways_but_not_access_tracks() -> None:
    assert "cycleway" in DEFAULT_CONFIG["road_types"]
    assert "track" not in DEFAULT_CONFIG["road_types"]
    assert DEFAULT_CONFIG["excluded_road_service_types"] == ["parking_aisle"]
    assert DEFAULT_CONFIG["excluded_road_access"] == []


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


def test_only_parking_aisles_are_filtered_from_urban_service_roads(
    tmp_path,
) -> None:
    roads_file = tmp_path / "urban-roads.geojson"
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
                                [-95.371, 29.7597],
                                [-95.369, 29.7597],
                            ],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {
                            "highway": "service",
                            "service": "parking_aisle",
                        },
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [-95.371, 29.7600],
                                [-95.369, 29.7600],
                            ],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {
                            "highway": "residential",
                            "access": "private",
                        },
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [-95.371, 29.7603],
                                [-95.369, 29.7603],
                            ],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {
                            "highway": "service",
                            "service": "alley",
                        },
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [-95.371, 29.7606],
                                [-95.369, 29.7606],
                            ],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    frame = MapFrame(
        center_lat=29.76015,
        center_lon=-95.370,
        coverage_width_m=300.0,
        coverage_height_m=180.0,
        print_width_mm=120.0,
        print_height_mm=90.0,
        margin_mm=5.0,
    )

    roads, mesh = download_and_build_roads(
        bbox=None,
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        road_types=["residential", "service"],
        road_widths={"residential": 1.1, "service": 0.8},
        road_height_mm=0.8,
        map_width_mm=frame.print_width_mm,
        map_height_mm=frame.print_height_mm,
        margin_mm=frame.margin_mm,
        roads_file=str(roads_file),
        excluded_service_types=DEFAULT_CONFIG["excluded_road_service_types"],
        excluded_access=DEFAULT_CONFIG["excluded_road_access"],
    )

    assert roads is not None
    assert mesh is not None

    def mapped_point(latitude: float) -> Point:
        coordinate = frame.transform_lonlat(
            np.asarray([latitude]),
            np.asarray([-95.370]),
        )[0]
        return Point(float(coordinate[0]), float(coordinate[1]))

    assert roads.covers(mapped_point(29.7597))
    assert not roads.covers(mapped_point(29.7600))
    assert roads.covers(mapped_point(29.7603))
    assert roads.covers(mapped_point(29.7606))


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


def test_dense_road_polygon_is_simplified_before_spatial_subdivision(monkeypatch):
    x = np.linspace(0.0, 100.0, 3_000)
    centerline = LineString(
        np.column_stack((x, 20.0 + 4.0 * np.sin(x) + 0.001 * np.sin(x * 50.0)))
    )
    polygon = centerline.buffer(0.8, resolution=8)
    original_vertices = len(polygon.exterior.coords)
    attempted_vertices = []

    def accept_only_simplified(candidate, height_mm, z_offset=0.0):
        attempted_vertices.append(len(candidate.exterior.coords))
        if len(candidate.exterior.coords) >= original_vertices:
            raise ValueError("synthetic dense-boundary failure")
        return Trimesh(
            vertices=np.array(
                [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                dtype=float,
            ),
            faces=np.array(
                [[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]],
                dtype=int,
            ),
            process=True,
        )

    monkeypatch.setattr(
        "memorymap_pipeline.roads.route_mesh_from_polygon",
        accept_only_simplified,
    )

    meshes = _extrude_road_polygon(
        polygon,
        height_mm=0.8,
        z_offset=-0.2,
    )

    assert len(meshes) == 1
    assert attempted_vertices[0] == original_vertices
    assert attempted_vertices[1] < original_vertices


def test_roads_remain_continuous_beneath_route_crossings(tmp_path) -> None:
    roads_file = tmp_path / "crossing-road.geojson"
    roads_file.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"highway": "primary"},
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
    roads, mesh = download_and_build_roads(
        bbox=None,
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        road_types=["primary"],
        road_widths={"primary": 2.0},
        road_height_mm=0.8,
        map_width_mm=frame.print_width_mm,
        map_height_mm=frame.print_height_mm,
        margin_mm=frame.margin_mm,
        roads_file=str(roads_file),
    )

    assert roads is not None
    route_crossing = box(58.0, 5.0, 62.0, 85.0)
    assert roads.intersection(route_crossing).area > 0.0
    assert mesh is not None and mesh.is_watertight


def test_large_connected_road_polygon_is_subdivided_after_extrusion_failure(
    monkeypatch,
) -> None:
    calls = []
    successful_areas = []

    def _fake_extrude(polygon, *, height_mm, z_offset):
        calls.append(polygon.area)
        if polygon.area > 600.0:
            raise ValueError("polygon is too complex to triangulate")
        successful_areas.append(polygon.area)
        mesh = Trimesh(
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
        mesh.apply_translation((polygon.centroid.x, polygon.centroid.y, z_offset))
        return mesh

    monkeypatch.setattr("memorymap_pipeline.roads.route_mesh_from_polygon", _fake_extrude)

    meshes = _extrude_road_polygon(
        box(0.0, 0.0, 100.0, 80.0),
        height_mm=0.8,
        z_offset=1.4,
    )

    assert calls[0] == 8000.0
    assert len(meshes) == 16
    assert len(successful_areas) == len(meshes)
    assert all(area <= 600.0 for area in successful_areas)
