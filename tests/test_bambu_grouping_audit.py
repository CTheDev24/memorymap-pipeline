import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point, box, mapping

from memorymap_pipeline.bambu_grouping_audit import audit_run, extrusion_layers, main


def test_full_circle_arc_and_retraction_are_distinguished():
    offset, layers = extrusion_layers(
        "; extruder_offset = 0x2\nM83\nG90\nG1 X1 Y0\n"
        "; Z_HEIGHT: 2.6\n; LINE_WIDTH: 0.4\n"
        "G2 I-1 J0 E0.1\nG1 X10 Y10 E-0.1\n"
    )
    assert offset == (0, 2)
    assert len(layers[2.6]) == 1
    assert layers[2.6][0].covers(Point(-1, 0))
    assert not layers[2.6][0].covers(Point(0, 0))


def test_absolute_extrusion_reset_and_relative_motion():
    _, layers = extrusion_layers(
        "; extruder_offset = 0x0\nM82\nG90\nG1 X0 Y0\n"
        "; Z_HEIGHT: 2.6\nG1 X1 E1\nG92 E0\n"
        "G91\nG1 X1 E0.5\nG1 X1 E0.4\n"
    )
    assert len(layers[2.6]) == 2
    assert layers[2.6][1].covers(Point(1.5, 0))


def test_audit_aligns_paths_with_x1_extruder_offset(tmp_path):
    report = {
        "specimens": [
            {
                "name": "control",
                "bounds_mm": [0, 0, 4, 4],
                "records": [
                    {
                        "group_id": "group-1",
                        "cut_by_crop": False,
                        "grouped_height_mm": 1.0,
                        "output_print_geometry": mapping(box(2, 2, 3, 3)),
                    }
                ],
            }
        ]
    }
    (tmp_path / "report.json").write_text(json.dumps(report))
    np.savez(tmp_path / "layer-meshes.npz", base_vertices=np.array([[0, 0, -1.6]]))
    directory = tmp_path / "bambu-x1c-pla" / "control"
    directory.mkdir(parents=True)
    with zipfile.ZipFile(directory / "sliced.3mf", "w") as archive:
        archive.writestr("Metadata/plate_1.json", json.dumps({"bbox_all": [10, 20, 14, 24]}))
        archive.writestr(
            "Metadata/plate_1.gcode",
            "; extruder_offset = 0x2\nM83\nG90\nG1 X12.1 Y20.5\n"
            "; Z_HEIGHT: 2.44\n; LINE_WIDTH: 0.4\nG1 X12.9 E0.1\n",
        )
    result = audit_run(tmp_path)
    observation = result["specimens"][0]["interior_groups"][0]
    assert observation["status"] == "sampled"
    assert observation["near_top_coverage_fraction"] > 0.35
    assert observation["minimum_coverage_fraction"] > 0
    assert len(observation["sampled_levels"]) == 5
    assert all(level["status"] == "paths_detected" for level in observation["sampled_levels"])
    assert result["specimens"][0]["extruder_offset_mm"] == (0, 2)


@pytest.mark.parametrize("header", ["", "; extruder_offset = 0x0,1x2\n"])
def test_missing_or_multiple_extruder_offsets_are_rejected(header):
    with pytest.raises(ValueError, match="single-extruder offset"):
        extrusion_layers(header)


@pytest.mark.parametrize("empty_layer", [False, True])
def test_audit_reports_missing_paths_on_some_sampled_levels(tmp_path, empty_layer):
    report = {
        "specimens": [
            {
                "name": "control",
                "bounds_mm": [0, 0, 4, 4],
                "records": [
                    {
                        "group_id": "group-1",
                        "group_source_ids": ["b1", "b2"],
                        "cut_by_crop": False,
                        "grouped_height_mm": 2.0,
                        "output_print_geometry": mapping(box(2, 2, 3, 3)),
                    }
                ],
            }
        ]
    }
    (tmp_path / "report.json").write_text(json.dumps(report))
    np.savez(tmp_path / "layer-meshes.npz", base_vertices=np.array([[0, 0, -1.6]]))
    directory = tmp_path / "bambu-x1c-pla" / "control"
    directory.mkdir(parents=True)
    with zipfile.ZipFile(directory / "sliced.3mf", "w") as archive:
        archive.writestr("Metadata/plate_1.json", json.dumps({"bbox_all": [10, 20, 14, 24]}))
        archive.writestr(
            "Metadata/plate_1.gcode",
            "; extruder_offset = 0x2\nM83\nG90\n"
            "; Z_HEIGHT: 2.10\n; LINE_WIDTH: 0.4\nG1 X10 Y20\n"
            "G1 X10.8 Y20 E0.1\n"
            "; Z_HEIGHT: 2.50\n; LINE_WIDTH: 0.4\nG1 X50 Y50\n" +
            ("G1 X50.8 Y50 E0.1\n" if not empty_layer else "") +
            "; Z_HEIGHT: 2.90\n; LINE_WIDTH: 0.4\nG1 X12.1 Y20.5\n"
            "G1 X12.9 Y20.5 E0.1\n",
        )
    result = audit_run(tmp_path)
    observation = result["specimens"][0]["interior_groups"][0]
    assert observation["status"] == "sampled"
    assert observation["source_member_count"] == 2
    assert observation["any_sample_without_paths"] is True
    assert any(level["status"] == "no_paths_detected" for level in observation["sampled_levels"])
    assert observation["near_top_coverage_fraction"] > 0


def test_cli_summary_counts_only_groups_with_coverage_at_all_sampled_levels(tmp_path, capsys, monkeypatch):
    report = {
        "specimens": [
            {
                "name": "control",
                "bounds_mm": [0, 0, 4, 4],
                "records": [
                    {
                        "group_id": "group-1",
                        "group_source_ids": ["b1", "b2"],
                        "cut_by_crop": False,
                        "grouped_height_mm": 2.0,
                        "output_print_geometry": mapping(box(2, 2, 3, 3)),
                    }
                ],
            }
        ]
    }
    (tmp_path / "report.json").write_text(json.dumps(report))
    np.savez(tmp_path / "layer-meshes.npz", base_vertices=np.array([[0, 0, -1.6]]))
    directory = tmp_path / "bambu-x1c-pla" / "control"
    directory.mkdir(parents=True)
    with zipfile.ZipFile(directory / "sliced.3mf", "w") as archive:
        archive.writestr("Metadata/plate_1.json", json.dumps({"bbox_all": [10, 20, 14, 24]}))
        archive.writestr(
            "Metadata/plate_1.gcode",
            "; extruder_offset = 0x2\nM83\nG90\n"
            "; Z_HEIGHT: 2.10\n; LINE_WIDTH: 0.4\nG1 X10 Y20\n"
            "G1 X10.8 Y20 E0.1\n"
            "; Z_HEIGHT: 2.50\n; LINE_WIDTH: 0.4\nG1 X50 Y50\n"
            "G1 X50.8 Y50 E0.1\n"
            "; Z_HEIGHT: 2.90\n; LINE_WIDTH: 0.4\nG1 X12.1 Y20.5\n"
            "G1 X12.9 Y20.5 E0.1\n",
        )
    monkeypatch.setattr("sys.argv", ["bambu_grouping_audit", str(tmp_path)])
    main()
    lines = [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines[0].startswith("control 1 0 with sampled coverage at all representative levels")
    assert lines[1] == str(Path(tmp_path) / "group-toolpath-coverage.json")
