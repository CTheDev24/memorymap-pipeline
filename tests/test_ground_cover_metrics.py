from __future__ import annotations

import pytest
from trimesh.creation import box

from memorymap_pipeline.ground_cover_metrics import (
    GroundCoverSignature,
    assert_signature_within_bands,
    ground_cover_signature,
    upward_projected_area_mm2,
)


def _body(width: float, height: float, z: float = 0.4):
    mesh = box(extents=(width, height, z))
    mesh.apply_translation((width / 2.0, height / 2.0, z / 2.0))
    return mesh


def test_upward_projection_measures_footprint_not_surface_or_side_area():
    mesh = _body(20.0, 10.0, 7.0)
    assert upward_projected_area_mm2(mesh) == pytest.approx(200.0)


def test_signature_is_compact_and_round_trips():
    signature = ground_cover_signature(
        print_width_mm=100.0,
        print_height_mm=50.0,
        vegetation_mesh=_body(50.0, 20.0),
        exposed_mesh=_body(20.0, 10.0),
        water_mesh=_body(100.0, 5.0),
    )
    assert signature.vegetation_fraction == pytest.approx(0.20)
    assert signature.exposed_fraction == pytest.approx(0.04)
    assert signature.water_fraction == pytest.approx(0.10)
    assert GroundCoverSignature.from_dict(signature.to_dict()) == signature


def test_golden_bands_allow_mesh_detail_changes_but_reject_coverage_regression():
    signature = GroundCoverSignature(100.0, 55.0, 25.0, 20.0)
    assert_signature_within_bands(
        signature,
        {
            "vegetation_fraction": (0.50, 0.60),
            "exposed_fraction": (0.20, 0.30),
            "water_fraction": (0.15, 0.25),
        },
    )
    with pytest.raises(AssertionError, match="exposed_fraction"):
        assert_signature_within_bands(signature, {"exposed_fraction": (0.0, 0.10)})


def test_signature_rejects_invalid_dimensions_and_unknown_bands():
    with pytest.raises(ValueError, match="Print dimensions"):
        ground_cover_signature(
            print_width_mm=0,
            print_height_mm=10,
            vegetation_mesh=None,
            exposed_mesh=None,
            water_mesh=None,
        )
    with pytest.raises(ValueError, match="Unknown"):
        assert_signature_within_bands(
            GroundCoverSignature(100, 10, 10, 10), {"sand_fraction": (0, 1)}
        )
