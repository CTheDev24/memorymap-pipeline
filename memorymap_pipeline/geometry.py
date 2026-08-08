from __future__ import annotations

from typing import Tuple

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
from shapely.validation import explain_validity

try:
    from shapely.ops import make_valid
except Exception:
    make_valid = None


def buffered_polygon_from_points(points: np.ndarray, route_width_mm: float) -> Polygon:
    """Create a buffered polygon from a sequence of 2D points (mm).

    The buffer is applied with half the route width as radius.
    """
    if points.shape[0] < 2:
        raise ValueError("Need at least two points to build a buffered polygon")

    line = LineString(points.tolist())
    radius = float(route_width_mm) / 2.0
    # Use a moderate resolution for round caps; join_style=1 (mitre) to preserve corners
    poly = line.buffer(radius, resolution=16, cap_style=2, join_style=1)
    # Dense GPX recordings can create thousands of sub-pixel boundary segments.
    # Trimesh's polygon triangulation may turn those into zero-area faces and open
    # the extrusion during cleanup. This tolerance is at most 1/200 of the route
    # width, well below printable resolution, while preserving route topology.
    tolerance = max(1e-4, float(route_width_mm) / 200.0)
    return poly.simplify(tolerance, preserve_topology=True)


def validate_polygon(polygon: Polygon) -> Tuple[bool, str]:
    """Return (is_valid, explanation)."""
    is_valid = polygon.is_valid
    explanation = explain_validity(polygon)
    return bool(is_valid), explanation


def repair_polygon(polygon: Polygon) -> Tuple[Polygon, bool, str]:
    """Attempt to repair an invalid polygon.

    Preference order:
    - shapely.ops.make_valid (if available)
    - unary_union/polygon.buffer(0) fallback
    Returns (repaired_polygon, is_valid, explanation).
    """
    if polygon.is_valid:
        return polygon, True, "Valid"

    repaired = None
    if make_valid is not None:
        try:
            repaired = make_valid(polygon)
        except Exception:
            repaired = None

    if repaired is None:
        try:
            repaired = polygon.buffer(0)
        except Exception:
            repaired = None

    if repaired is None:
        # final attempt: unary_union of polygon components
        try:
            repaired = unary_union(polygon)
        except Exception:
            repaired = polygon

    is_valid, explanation = validate_polygon(repaired)
    return repaired, is_valid, explanation
