import numpy as np
from trimesh.creation import box
from trimesh.util import concatenate

from memorymap_pipeline.mesh import build_base_plate
from memorymap_pipeline.printability import PrintabilityProfile, audit_printability


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


def test_empty_model_is_the_only_kind_of_blocking_preflight_failure() -> None:
    report = audit_printability({})

    assert report.status == "red"
    assert not report.exportable
    assert report.blocking_issues[0].code == "empty_model"


def test_topology_error_is_red_but_remains_exportable_for_user_review() -> None:
    first = _solid((4.0, 4.0, 2.0), (10.0, 10.0, 0.0))
    combined = concatenate((first, first.copy()))
    combined.merge_vertices()

    report = audit_printability({"base": combined})

    assert report.status == "red"
    assert report.exportable
    report.raise_for_errors()


def test_profile_warns_about_sub_two_line_xy_features() -> None:
    base = build_base_plate(40.0, 30.0, 1.6)
    route = _solid((8.0, 0.6, 2.2), (20.0, 15.0, 0.9))

    report = audit_printability(
        {"base": base, "route": route},
        profile=PrintabilityProfile(nozzle_diameter_mm=0.4, minimum_xy_feature_mm=0.8),
        declared_feature_widths_mm={"route": 0.6},
    )

    assert report.status == "yellow"
    assert report.exportable
    assert any(issue.code == "thin_xy_feature" for issue in report.issues)


def test_margin_and_z_bound_violations_are_reported() -> None:
    base = build_base_plate(40.0, 30.0, 1.6)
    route = _solid((8.0, 1.2, 2.2), (3.0, 15.0, -1.0))

    report = audit_printability(
        {"base": base, "route": route},
        print_size_mm=(40.0, 30.0),
        margin_mm=5.0,
        declared_feature_widths_mm={"route": 1.2},
    )

    codes = {issue.code for issue in report.issues}
    assert "outside_printable_margin" in codes
    assert "outside_z_bounds" in codes
    assert report.status == "red"


def test_disconnected_route_sections_are_reported_without_blocking_export() -> None:
    base = build_base_plate(40.0, 30.0, 1.6)
    route = concatenate(
        (
            _solid((8.0, 1.2, 2.2), (10.0, 15.0, 0.9)),
            _solid((8.0, 1.2, 2.2), (30.0, 15.0, 0.9)),
        )
    )

    report = audit_printability({"base": base, "route": route})

    assert any(issue.code == "route_discontinuity" for issue in report.issues)
    assert report.status == "red"
    assert report.exportable
    assert report.to_dict()["status"] == "red"
