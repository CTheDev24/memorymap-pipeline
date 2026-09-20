import json
import zipfile

import numpy as np
import pytest
from shapely.geometry import Point, box, mapping

from memorymap_pipeline.bambu_grouping_audit import audit_run, extrusion_layers


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
    assert observation["status"] == "paths_detected"
    assert observation["covered_area_fraction"] > 0.35
    assert result["specimens"][0]["extruder_offset_mm"] == (0, 2)


@pytest.mark.parametrize("header", ["", "; extruder_offset = 0x0,1x2\n"])
def test_missing_or_multiple_extruder_offsets_are_rejected(header):
    with pytest.raises(ValueError, match="single-extruder offset"):
        extrusion_layers(header)
