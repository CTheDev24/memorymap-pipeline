import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box
from shapely.ops import unary_union
from trimesh.creation import box as solid_box

from memorymap_pipeline.footprint_benchmark import (
    _grouping_advisory_metrics,
    _sha,
    compare,
    crop_mesh,
    freeze,
    review,
    run,
    screen_footprint,
    synthetic,
)
from memorymap_pipeline.map_frame import MapFrame

FIXTURES = Path(__file__).parent / "fixtures"


def test_local_width_detects_neck_despite_large_global_dimensions():
    polygon = unary_union([box(0, 0, 3, 3), box(6, 0, 9, 3), box(3, 1, 6, 1.3)])
    result = screen_footprint(polygon)
    assert result["status"] == "at_risk"
    assert not result["core_empty"]
    assert result["lost_area_fraction"] > 0
    assert screen_footprint(box(0, 0, 0.2, 0.2))["core_empty"]
    assert screen_footprint(box(0, 0, 0.8, 0.8))["status"] == "not_flagged"
    courtyard = box(0, 0, 7, 7).difference(box(0.3, 0.3, 6.7, 6.7))
    assert screen_footprint(courtyard)["core_empty"]


def test_grouping_advisory_metrics_capture_small_density_and_fragmentation_proxy():
    frame = MapFrame(
        center_lat=41.88,
        center_lon=-87.63,
        coverage_width_m=2000,
        coverage_height_m=2000,
        print_width_mm=190,
        print_height_mm=240,
    )
    shared_group = box(0, 0, 1.1, 0.9)
    diagnostics = [
        {
            "id": "b1",
            "status": "grouped",
            "source_print_geometry": box(0, 0, 0.2, 0.2).__geo_interface__,
            "output_print_geometry": shared_group.__geo_interface__,
        },
        {
            "id": "b2",
            "status": "grouped",
            "source_print_geometry": box(0.35, 0, 0.55, 0.2).__geo_interface__,
            "output_print_geometry": shared_group.__geo_interface__,
        },
        {
            "id": "b3",
            "status": "retained",
            "source_print_geometry": box(2, 2, 3, 3).__geo_interface__,
            "output_print_geometry": box(2, 2, 3, 3).__geo_interface__,
        },
    ]
    metrics = _grouping_advisory_metrics(
        frame,
        diagnostics,
        {"enabled": True, "grouped_sources": 2, "ungrouped_small_sources": 1},
    )
    assert metrics["total_source_footprints"] == 3
    assert metrics["small_footprints"]["count"] == 2
    assert metrics["small_footprints"]["centroid_isolated_within_0_4mm_count"] == 0
    assert metrics["grouped_source_count"] == 2
    assert metrics["remaining_small_source_count"] == 1
    assert metrics["building_extrusion_islands"]["before_grouping"] == 3
    assert metrics["building_extrusion_islands"]["after_grouping"] == 2
    assert metrics["fragmentation_proxy"]["before_grouping"] == 3
    assert metrics["fragmentation_proxy"]["after_grouping"] == 2


def test_crop_preserves_z_scale_caps_cut_and_does_not_mutate_input():
    mesh = solid_box(extents=(8, 6, 4))
    mesh.apply_translation((10, 10, 1.8))
    original = mesh.vertices.copy()
    cropped = crop_mesh(mesh, (8, 9, 12, 14))
    assert cropped.is_watertight
    np.testing.assert_allclose(cropped.bounds, [[0, 0, -0.2], [4, 4, 3.8]])
    assert cropped.volume == pytest.approx(4 * 4 * 4)
    np.testing.assert_array_equal(mesh.vertices, original)
    assert crop_mesh(mesh, (30, 30, 40, 40)) is None


def test_synthetic_controls_cannot_pass_without_external_evidence(tmp_path):
    output = tmp_path / "synthetic"
    report = json.loads(synthetic(output).read_text())
    assert len(report["specimens"]) == 11
    tiny = report["specimens"][0]
    assert tiny["structural_audit_issues"] == []
    assert tiny["records"][0]["screening"]["core_empty"]
    assert len(list(output.glob("*.3mf"))) == 11
    assert review(output)["status"] == "not_accepted"
    with pytest.raises(FileExistsError):
        synthetic(output)


def test_crop_caps_multiple_separate_loops_without_filling_courtyard():
    from memorymap_pipeline.mesh import route_mesh_from_polygon

    polygon = box(0, 0, 10, 10).difference(box(2, 2, 8, 8))
    mesh = route_mesh_from_polygon(polygon, 2, 0)
    cropped = crop_mesh(mesh, (5, 0, 10, 10))
    assert cropped.is_watertight
    assert cropped.volume == pytest.approx(64)


def _offline_spec(tmp_path):
    sources = {
        role: {
            "path": str(FIXTURES / name),
            "origin": "repository test fixture",
            "acquired_at": "2026-09-19",
            "license": "test data",
        }
        for role, name in [
            ("route", "frame_route.gpx"),
            ("buildings", "frame_buildings.geojson"),
            ("roads", "frame_roads.geojson"),
            ("water", "frame_water.geojson"),
        ]
    }
    path = tmp_path / "spec.json"
    path.write_text(
        json.dumps(
            {
                "name": "offline-test",
                "sources": sources,
                "crops": [{"name": "center", "bounds_mm": [70, 90, 120, 150]}],
            }
        )
    )
    return path


def test_frozen_run_is_offline_and_preserves_full_frame(tmp_path, monkeypatch):
    import requests

    def no_network(*args, **kwargs):
        pytest.fail("Benchmark attempted a network request")

    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    manifest = freeze(_offline_spec(tmp_path), tmp_path / "snapshot")
    frozen = json.loads(manifest.read_text())
    assert frozen["frame"]["margin_mm"] == 0
    assert frozen["route_clearance_mm"] == 5
    assert not frozen["config"]["flat_border_enabled"]
    from shapely.geometry import LineString

    from memorymap_pipeline.gpx_loader import load_route_from_gpx
    from memorymap_pipeline.map_frame import MapFrame

    points = load_route_from_gpx(manifest.parent / "route.gpx").points
    outline = LineString(MapFrame(**frozen["frame"]).transform_points(points)).buffer(0.6)
    x0, y0, x1, y1 = outline.bounds
    assert min(x0, y0, 190 - x1, 240 - y1) == pytest.approx(5, abs=0.01)
    output = tmp_path / "baseline"
    report = json.loads(run(manifest, output).read_text())
    assert report["frame"] == frozen["frame"]
    assert report["manifest_sha256"] == _sha(manifest)
    assert report["full_export_removed_building_faces"] == 0
    assert report["specimens"][0]["export_removed_building_faces"] == 0
    assert (output / "center.3mf").exists()
    records = json.loads((output / "source-mapping.json").read_text())
    assert records
    assert any(record["output_face_ranges"] for record in records)
    original = tmp_path / "snapshot" / "buildings.geojson"
    original.write_text(original.read_text() + "\n")
    with pytest.raises(ValueError, match="checksum"):
        run(manifest, tmp_path / "tampered")
    assert not (tmp_path / "tampered").exists()


def test_comparison_rejects_different_snapshots_and_exposes_missing_ids(tmp_path):
    report = {
        "manifest_sha256": "a",
        "specimens": [{"name": "x", "records": [{"id": "b1", "status": "retained"}]}],
    }
    baseline, candidate = tmp_path / "a.json", tmp_path / "b.json"
    baseline.write_text(json.dumps(report))
    report["specimens"][0]["records"] = []
    candidate.write_text(json.dumps(report))
    assert compare(baseline, candidate)["missing_source_ids"] == [("x", "b1")]
    report["manifest_sha256"] = "b"
    candidate.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="different source"):
        compare(baseline, candidate)


def test_review_requires_exact_ids_model_and_profile(tmp_path):
    output = tmp_path / "run"
    synthetic(output)
    path = output / "evidence.json"
    evidence = json.loads(path.read_text())
    evidence.update(printer="test", material="test", slicer_version="test")
    for name in ("project.3mf", "toolpath.gcode", "photo.png"):
        (output / name).write_bytes(b"test artifact placeholder")
    evidence.update(
        slicer_project="project.3mf", sliced_toolpaths="toolpath.gcode", print_photos=["photo.png"]
    )
    for row in evidence["observations"]:
        row.update(slicer="pass", physical_print="pass", barriers="pass")
    path.write_text(json.dumps(evidence))
    assert review(output)["status"] == "accepted"
    evidence["observations"].pop()
    path.write_text(json.dumps(evidence))
    assert review(output)["status"] == "not_accepted"
    evidence["layer_height_mm"] = 0.2
    path.write_text(json.dumps(evidence))
    assert "Profile mismatch: layer_height_mm" in review(output)["problems"]
    model = next(output.glob("01-*.3mf"))
    model.write_bytes(b"modified")
    assert any("Model changed" in p for p in review(output)["problems"])


def test_capture_assembles_split_relation_rings_and_preserves_courtyard():
    from memorymap_pipeline.capture_chicago_benchmark import _polygon

    def member(points, role):
        return {"role": role, "geometry": [{"lon": x, "lat": y} for x, y in points]}

    polygon = _polygon(
        {
            "type": "relation",
            "members": [
                member([(0, 0), (10, 0), (10, 10)], "outer"),
                member([(10, 10), (0, 10), (0, 0)], "outer"),
                member([(2, 2), (8, 2), (8, 8), (2, 8), (2, 2)], "inner"),
            ],
        }
    )
    assert polygon.is_valid
    assert polygon.area == pytest.approx(64)
    assert len(polygon.interiors) == 1


def test_failed_export_preserves_report_and_source_mapping(tmp_path, monkeypatch):
    from shapely.geometry import mapping

    from memorymap_pipeline import footprint_benchmark as benchmark
    from memorymap_pipeline.generation import GenerationResult
    from memorymap_pipeline.mesh import build_base_plate

    def generated(request, **kwargs):
        assert request.export_model is False
        building = solid_box((2, 2, 2)).subdivide()
        building.apply_translation((90, 110, 5))
        request.building_diagnostics.append(
            {
                "id": "b1",
                "status": "retained",
                "source_id": "1",
                "output_print_geometry": mapping(box(89, 109, 91, 111)),
                "output_face_ranges": [[0, len(building.faces)]],
            }
        )
        return GenerationResult(
            Path(request.output_path),
            [],
            {},
            base_mesh=build_base_plate(190, 240, 1.6),
            buildings_mesh=building,
        )

    monkeypatch.setattr(benchmark, "generate_memory_map", generated)
    manifest = freeze(_offline_spec(tmp_path), tmp_path / "snapshot")
    output = tmp_path / "failed"
    report = json.loads(run(manifest, output).read_text())
    assert "floating" in report["full_export_error"]
    assert "floating" in report["specimens"][0]["export_error"]
    assert not (output / "center.3mf").exists()
    assert (output / "center.svg").exists()
    assert (output / "source-mapping.json").exists()
    assert (output / "building-mesh.npz").exists()
    assert review(output)["status"] == "not_accepted"


def test_bambu_preset_resolution_and_toolpath_summary(tmp_path):
    import zipfile

    from memorymap_pipeline.bambu_benchmark import resolve_preset, toolpath_summary

    presets = tmp_path / "machine"
    presets.mkdir()
    (presets / "base.json").write_text(json.dumps({"speed": "1", "nozzle": "0.4"}))
    (presets / "code.json").write_text(json.dumps({"start_gcode": "test"}))
    child = presets / "child.json"
    child.write_text(json.dumps({"inherits": "base", "include": ["code"], "speed": "2"}))
    assert resolve_preset(tmp_path, "machine", "child") == {
        "speed": "2",
        "nozzle": "0.4",
        "start_gcode": "test",
    }
    child.write_text(json.dumps({"inherits": "child"}))
    with pytest.raises(ValueError, match="cycle"):
        resolve_preset(tmp_path, "machine", "child")
    project = tmp_path / "slice.3mf"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "; Z_HEIGHT: 0.2\n; Z_HEIGHT: 0.36\n")
    assert toolpath_summary(project)["plates"][0]["layer_count"] == 2
    assert toolpath_summary(project)["plates"][0]["last_layer_z_mm"] == 0.36


def test_bambu_records_a_run_with_all_specimens_blocked(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from memorymap_pipeline import bambu_benchmark as bambu

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "profile": {"nozzle_diameter_mm": 0.4, "layer_height_mm": 0.16},
                "specimens": [{"name": "failed", "export_error": "floating"}],
            }
        )
    )
    monkeypatch.setattr(
        bambu, "resolve_preset", lambda *args: {"nozzle_diameter": ["0.4"], "layer_height": "0.16"}
    )
    monkeypatch.setattr(
        bambu.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="BambuStudio-test:")
    )
    executable = tmp_path / "fake-executable"
    executable.write_text("test")
    result = bambu.slice_run(run_dir, tmp_path / "out", executable, tmp_path, "m", "p", "f")
    assert json.loads(result.read_text())["specimens"] == [
        {"specimen": "failed", "status": "blocked_by_geometry"}
    ]


def test_chicago_collapsed_crop_faces_preserve_volume():
    from trimesh import Trimesh

    data = json.loads((FIXTURES / "chicago_geometry_regressions.json").read_text())["crop"]
    original = Trimesh(vertices=data["vertices"], faces=data["faces"], process=False)
    cropped = crop_mesh(original, data["bounds"])
    assert cropped.is_watertight
    assert cropped.volume == pytest.approx(0.013254935128763634, abs=1e-12)
    assert cropped.bounds[0, 2] == pytest.approx(-0.2)
    assert cropped.bounds[1, 2] == pytest.approx(1.2)


def test_chicago_touching_courtyard_and_near_collinear_footprints():
    from shapely.geometry import shape

    from memorymap_pipeline.mesh import route_mesh_from_polygon

    data = json.loads((FIXTURES / "chicago_geometry_regressions.json").read_text())
    for record in data["footprints"]:
        polygon = shape(record["geometry"])
        mesh = route_mesh_from_polygon(polygon, 1.4, -0.2)
        assert mesh.is_watertight, record["id"]
        assert mesh.volume == pytest.approx(polygon.area * 1.4, abs=1e-10)
        # The horizontal top surfaces must reproduce the courtyard and outline.
        from shapely.geometry import Polygon
        top = [Polygon(t[:, :2]) for t in mesh.triangles if np.allclose(t[:, 2], 1.2)]
        assert unary_union(top).symmetric_difference(polygon).area < 1e-10


def test_support_requires_contact_after_rendered_height_mapping():
    from memorymap_pipeline.buildings import _building_dimensions, _supported_elevated_elements

    def dims(tags):
        return _building_dimensions(tags, default_height_m=6, levels_to_m=3, max_height_m=400)

    elements = [
        (box(0, 0, 2, 2), dims({"height": 60}), True, None),
        (box(0, 0, 1, 1), dims({"height": 160, "min_height": 55}), True, None),
    ]
    assert 1 in _supported_elevated_elements(elements)
    assert _supported_elevated_elements(elements, [(0, 3), (4, 12)])[1] == 3
    assert _supported_elevated_elements(elements, [(0, 4), (4, 12)])[1] == 4


def test_route_margin_measures_from_outer_route_edge():
    from shapely.geometry import LineString

    from memorymap_pipeline.gpx_loader import load_route_from_gpx
    from memorymap_pipeline.map_frame import MapFrame

    route = load_route_from_gpx(FIXTURES / "frame_route.gpx")
    frame = MapFrame.fit_route(route.points, 190, 240, margin_mm=5, route_padding_mm=0.6)
    outline = LineString(frame.transform_points(route.points)).buffer(0.6)
    x0, y0, x1, y1 = outline.bounds
    clearances = [x0, y0, 190 - x1, 240 - y1]
    assert min(clearances) == pytest.approx(5, abs=0.01)
    assert all(clearance >= 5 - 0.01 for clearance in clearances)


def test_numerical_fragment_filter_is_not_a_printability_size_filter():
    from memorymap_pipeline.buildings import _is_numerical_fragment

    assert _is_numerical_fragment(box(100, 100, 100 + 2 * np.spacing(100.0), 101))
    assert not _is_numerical_fragment(box(100, 100, 100.000001, 101))
    assert not _is_numerical_fragment(box(100, 100, 100.2, 100.2))
