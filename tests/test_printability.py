import numpy as np
from trimesh.creation import box
from trimesh.util import concatenate

from memorymap_pipeline.mesh import build_base_plate
from memorymap_pipeline.printability import audit_printability


def _solid(extents, translation):
    mesh = box(extents=extents)
    mesh.apply_translation(translation)
    return mesh


def test_base_plate_is_watertight_and_manifold() -> None:
    base = build_base_plate(40.0, 30.0, 1.6)
    report = audit_printability({"base": base})

    assert base.is_watertight
    assert report.printable


def test_embedded_feature_has_support_path_to_base() -> None:
    base = build_base_plate(40.0, 30.0, 1.6)
    route = _solid((8.0, 1.2, 2.2), (20.0, 15.0, 0.9))

    report = audit_printability({"base": base, "route": route})

    assert report.printable


def test_floating_feature_is_rejected() -> None:
    base = build_base_plate(40.0, 30.0, 1.6)
    route = _solid((8.0, 1.2, 2.0), (20.0, 15.0, 2.0))

    report = audit_printability({"base": base, "route": route})

    assert not report.printable
    assert any(issue.code == "floating_components" for issue in report.issues)


def test_roof_can_reach_base_through_supported_body() -> None:
    base = build_base_plate(40.0, 30.0, 1.6)
    body = _solid((8.0, 8.0, 2.2), (20.0, 15.0, 0.9))
    roof = _solid((8.0, 8.0, 1.0), (20.0, 15.0, 2.5))
    buildings = concatenate((body, roof))

    report = audit_printability({"base": base, "buildings": buildings})

    assert report.printable


def test_over_connected_edges_are_rejected() -> None:
    first = _solid((4.0, 4.0, 2.0), (10.0, 10.0, 0.0))
    second = first.copy()
    combined = concatenate((first, second))
    combined.merge_vertices()

    report = audit_printability({"base": combined})

    assert not report.printable
    assert any(issue.code == "non_manifold" for issue in report.issues)
    assert np.count_nonzero(np.bincount(combined.edges_unique_inverse) > 2) > 0


def test_inconsistent_face_winding_is_rejected() -> None:
    base = build_base_plate(40.0, 30.0, 1.6)
    base.faces[0] = base.faces[0][::-1]

    report = audit_printability({"base": base})

    assert not report.printable
    assert any(issue.code == "inconsistent_winding" for issue in report.issues)
