from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import box

from memorymap_pipeline.route_markers import (
    RouteMarkerMode,
    build_route_marker,
    marker_centers,
)


def test_marker_mode_validation_and_selection():
    assert RouteMarkerMode.parse("BOTH") is RouteMarkerMode.BOTH
    assert RouteMarkerMode.START.includes_start
    assert not RouteMarkerMode.START.includes_finish
    with pytest.raises(ValueError, match="Unsupported route marker mode"):
        RouteMarkerMode.parse("middle")


def test_terrain_aware_marker_is_watertight_and_spans_rim_support():
    def support(x, y):
        return 2.0 + 0.1 * np.asarray(x) + 0.2 * np.asarray(y)

    mesh, reason = build_route_marker(
        (10.0, 12.0), support, box(0, 0, 30, 30),
        diameter_mm=4.0, visible_height_mm=1.0, embed_depth_mm=0.25,
    )

    assert reason is None
    assert mesh is not None and mesh.is_watertight
    assert mesh.metadata["marker_bottom_mm"] == pytest.approx(
        mesh.metadata["support_min_mm"] - 0.25
    )
    assert mesh.metadata["marker_top_mm"] == pytest.approx(
        mesh.metadata["support_max_mm"] + 1.0
    )
    assert mesh.bounds[0, 2] == pytest.approx(mesh.metadata["marker_bottom_mm"])
    assert mesh.bounds[1, 2] == pytest.approx(mesh.metadata["marker_top_mm"])


def test_off_frame_marker_is_omitted_without_partial_geometry():
    mesh, reason = build_route_marker(
        (0.5, 5.0), lambda x, y: np.zeros_like(x), box(0, 0, 10, 10),
        diameter_mm=2.0,
    )
    assert mesh is None
    assert reason == "marker footprint falls outside the printable region"


def test_near_loop_deduplicates_both_markers():
    centers = marker_centers((5, 5), (5.2, 5.1), "both", 0.5)
    assert [name for name, _center in centers] == ["start"]

    distinct = marker_centers((5, 5), (8, 5), "both", 0.5)
    assert [name for name, _center in distinct] == ["start", "finish"]


def test_marker_input_validation():
    with pytest.raises(ValueError, match="dimensions"):
        build_route_marker((1, 1), lambda x, y: x, box(0, 0, 2, 2), diameter_mm=0)
    with pytest.raises(ValueError, match="one finite height"):
        build_route_marker((5, 5), lambda x, y: np.array([0.0]), box(0, 0, 10, 10))
    with pytest.raises(ValueError, match="non-negative"):
        marker_centers((0, 0), (1, 1), "both", -1)
