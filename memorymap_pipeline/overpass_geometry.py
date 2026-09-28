"""Assemble OSM building rings before performing polygon operations."""
from __future__ import annotations

import logging

from shapely import make_valid
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import linemerge, unary_union
from shapely.errors import GEOSException


def _polygonal(geometry):
    if geometry.is_empty:
        return None
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry if not geometry.is_empty else None
    parts = []
    for child in getattr(geometry, "geoms", ()):
        polygon = _polygonal(child)
        if polygon is not None:
            parts.append(polygon)
    return unary_union(parts) if parts else None


def _coordinates(items):
    if not isinstance(items, list):
        return []
    # Never connect across missing coordinates in a partial server response.
    if any(not isinstance(p, dict) or "lon" not in p or "lat" not in p for p in items):
        return []
    return [(p["lon"], p["lat"]) for p in items]


def _rings(lines):
    if not lines:
        return None
    # Members may be reversed, unordered, and only fragments of a ring.
    merged = linemerge(MultiLineString(lines))
    chains = [merged] if merged.geom_type == "LineString" else list(merged.geoms)
    polygons = []
    for chain in chains:
        points = list(chain.coords)
        if len(points) < 4 or points[0] != points[-1]:
            raise ValueError("relation contains an incomplete ring")
        polygon = _polygonal(Polygon(points))
        if polygon is not None:
            polygons.append(polygon)
    return _polygonal(unary_union(polygons)) if polygons else None


def polygon_from_element(element):
    """Return valid polygonal geometry, or log an explicitly unresolved source."""
    try:
        if element.get("type") == "way":
            points = _coordinates(element.get("geometry"))
            if len(points) < 4 or points[0] != points[-1]:
                raise ValueError("building way contains an incomplete ring")
            return _polygonal(Polygon(points))
        if element.get("type") != "relation":
            return None
        outer, inner = [], []
        for member in element.get("members", []):
            if member.get("type") != "way":
                raise ValueError("relation contains an unsupported non-way member")
            points = _coordinates(member.get("geometry"))
            if len(points) < 2:
                raise ValueError("relation member has missing coordinates")
            if member.get("role", "") not in {"", "outer", "inner"}:
                raise ValueError("relation member has an unsupported role")
            (inner if member.get("role") == "inner" else outer).append(LineString(points))
        result = _rings(outer)
        holes = _rings(inner)
        if result is not None and holes is not None:
            result = _polygonal(result.difference(holes))
        return result
    except (GEOSException, ValueError, TypeError, KeyError) as exc:
        logging.warning("Unresolved building %s/%s: %s", element.get("type"), element.get("id"), exc)
        return None
