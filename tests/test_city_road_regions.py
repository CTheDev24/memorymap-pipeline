from pathlib import Path

import pytest
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, box

from memorymap_pipeline import generation
from memorymap_pipeline.config import load_config
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.roads import _extrude_road_regions


def test_mixed_road_regions_keep_polygons_holes_and_height_bands():
    major = box(0, 0, 10, 10).difference(box(3, 3, 7, 7))
    local = box(20, 0, 25, 5)
    regions = [
        (GeometryCollection([major, LineString([(10, 0), (12, 0)]), Point(12, 0)]), 0.6),
        (GeometryCollection([GeometryCollection([MultiPolygon([local])]), Point(25, 5)]), 0.3),
    ]
    meshes = _extrude_road_regions(regions, hierarchical=True, embed_depth_mm=0.2, z_offset=1.4)
    assert len(meshes) == 2
    for mesh, polygon, visible in zip(meshes, (major, local), (0.6, 0.3), strict=True):
        assert mesh.is_watertight and mesh.is_winding_consistent
        assert mesh.volume == pytest.approx(polygon.area * (visible + 0.2))
        assert mesh.bounds[0, 2] == pytest.approx(1.4)
        assert mesh.bounds[1, 2] == pytest.approx(1.6 + visible)


def test_zero_area_contacts_are_successfully_empty():
    assert not _extrude_road_regions(
        [(GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 0)])]), 0.6)],
        hierarchical=True,
        embed_depth_mm=0.2,
        z_offset=1.4,
    )


def test_failed_road_fragment_is_recorded_without_losing_its_geometry(monkeypatch):
    from memorymap_pipeline import roads

    def fail(*args, **kwargs):
        raise ValueError("test triangulation failure")

    monkeypatch.setattr(roads, "route_mesh_from_polygon", fail)
    polygon = box(0, 0, 0.1, 0.05)
    stats = {}
    assert not _extrude_road_regions(
        [(polygon, 0.6)],
        hierarchical=True,
        embed_depth_mm=0.2,
        z_offset=1.4,
        processing_stats=stats,
    )
    assert stats["failed_polygon_count"] == 1
    assert stats["failed_area_mm2"] == pytest.approx(polygon.area)
    assert stats["failed_polygons"][0]["geometry"]["type"] == "Polygon"


def test_partial_road_processing_cannot_export_as_complete(tmp_path, monkeypatch):
    def partial(**kwargs):
        kwargs["processing_stats"]["failed_polygon_count"] = 1
        return box(10, 10, 11, 11), generation.build_base_plate(2, 2, 0.5)

    monkeypatch.setattr(generation, "download_and_build_roads", partial)
    output = tmp_path / "incomplete.3mf"
    request = generation.GenerationRequest(
        route=load_route_from_gpx(Path(__file__).parent / "fixtures/frame_route.gpx"),
        frame=MapFrame(
            center_lat=29.76,
            center_lon=-95.37,
            coverage_width_m=180,
            coverage_height_m=140,
            print_width_mm=120,
            print_height_mm=90,
            margin_mm=5,
        ),
        output_path=output,
        include_buildings=False,
        config=load_config(),
    )
    with pytest.raises(generation.LayerGenerationError) as caught:
        generation.generate_memory_map(request)
    assert caught.value.layer_states == {"roads": "failed"}
    assert not output.exists() and not output.with_suffix(".audit.json").exists()


def test_nonempty_road_meshing_failure_preserves_previous_result(tmp_path, monkeypatch):
    output = tmp_path / "previous.3mf"
    audit = output.with_suffix(".audit.json")
    output.write_bytes(b"previous successful model")
    audit.write_text("previous successful audit")
    monkeypatch.setattr(
        generation, "download_and_build_roads", lambda **kwargs: (box(10, 10, 11, 11), None)
    )
    request = generation.GenerationRequest(
        route=load_route_from_gpx(Path(__file__).parent / "fixtures/frame_route.gpx"),
        frame=MapFrame(
            center_lat=29.76,
            center_lon=-95.37,
            coverage_width_m=180,
            coverage_height_m=140,
            print_width_mm=120,
            print_height_mm=90,
            margin_mm=5,
        ),
        output_path=output,
        include_buildings=False,
        config=load_config(),
    )
    with pytest.raises(generation.LayerGenerationError) as caught:
        generation.generate_memory_map(request)
    assert caught.value.layer_states == {"roads": "failed"}
    assert output.read_bytes() == b"previous successful model"
    assert audit.read_text() == "previous successful audit"
