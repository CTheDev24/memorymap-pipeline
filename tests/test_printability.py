import numpy as np
from trimesh.creation import box
from trimesh.util import concatenate

from memorymap_pipeline.mesh import build_base_plate, split_overconnected_vertex_fans
from memorymap_pipeline.printability import (
    audit_printability,
    remove_small_floating_components,
)


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


def test_only_tiny_proven_floating_building_shells_are_removed() -> None:
    base = build_base_plate(40.0, 30.0, 1.6)
    grounded = _solid((4.0, 4.0, 2.2), (10.0, 10.0, 0.9))
    supported_roof = _solid((4.0, 4.0, 0.4), (10.0, 10.0, 2.1))
    tiny_floating = _solid((1.0, 1.0, 0.4), (25.0, 10.0, 3.0))
    buildings = concatenate((grounded, supported_roof, tiny_floating))

    cleaned, removed = remove_small_floating_components(
        {"base": base, "buildings": buildings},
        "buildings",
        maximum_faces=12,
    )

    assert removed == 1
    assert cleaned is not None
    assert audit_printability({"base": base, "buildings": cleaned}).printable
    assert cleaned.bounds[1, 0] < 25.0


def test_large_floating_building_shell_remains_a_validation_error() -> None:
    base = build_base_plate(40.0, 30.0, 1.6)
    floating = _solid((4.0, 4.0, 1.0), (20.0, 15.0, 4.0))
    floating = floating.subdivide()

    cleaned, removed = remove_small_floating_components(
        {"base": base, "buildings": floating},
        "buildings",
        maximum_faces=12,
    )

    assert removed == 0
    assert cleaned is floating
    assert not audit_printability({"base": base, "buildings": cleaned}).printable


def test_over_connected_edges_are_rejected() -> None:
    first = _solid((4.0, 4.0, 2.0), (10.0, 10.0, 0.0))
    second = first.copy()
    combined = concatenate((first, second))
    combined.merge_vertices()

    report = audit_printability({"base": combined})

    assert not report.printable
    assert any(issue.code == "non_manifold" for issue in report.issues)
    assert np.count_nonzero(np.bincount(combined.edges_unique_inverse) > 2) > 0


def test_coincident_closed_shell_edges_are_split_into_manifold_fans() -> None:
    first = _solid((10.0, 10.0, 2.0), (0.0, 0.0, 0.0))
    second = _solid((10.0, 10.0, 2.0), (10.0, 10.0, 0.0))
    combined = concatenate((first, second))
    combined.merge_vertices()

    before = np.bincount(combined.edges_unique_inverse)
    repaired = split_overconnected_vertex_fans(combined)
    after = np.bincount(repaired.edges_unique_inverse)

    assert np.count_nonzero(before > 2) == 1
    assert np.count_nonzero(after == 1) == 0
    assert np.count_nonzero(after > 2) == 0
    assert repaired.is_watertight
    assert repaired.is_winding_consistent


def test_inconsistent_face_winding_is_rejected() -> None:
    base = build_base_plate(40.0, 30.0, 1.6)
    base.faces[0] = base.faces[0][::-1]

    report = audit_printability({"base": base})

    assert not report.printable
    assert any(issue.code == "inconsistent_winding" for issue in report.issues)
