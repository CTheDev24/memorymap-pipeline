import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box
from shapely.ops import unary_union
from trimesh.creation import box as solid_box

from memorymap_pipeline.footprint_benchmark import (
    _sha,
    compare,
    crop_mesh,
    freeze,
    review,
    run,
    screen_footprint,
    synthetic,
)

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
    assert frozen["frame"]["margin_mm"] == 5
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
