"""Deterministic print-space thinning of redundant local corridors.

Select whole named corridors, never synthetic streets or clipped pieces. Sparse
grids survive unchanged. Major streets and corridors near the route are protected.
The occupied index is a broad phase only; decisions use exact geometry distances
and length coverage. This is an experimental presentation algorithm, not routing.
"""
from collections import defaultdict
from hashlib import sha256
from math import floor, isfinite

from shapely.geometry import box
from shapely.ops import unary_union

from .map_styles import ROAD_CLASSES, road_class

ALGORITHM = "street-selection-v1"


def select_streets(records, route_region, widths, spacing_mm=.7):
    if not isfinite(spacing_mm) or spacing_mm <= 0:
        raise ValueError("Street spacing must be positive and finite")
    groups = defaultdict(list)
    for index, record in enumerate(records):
        line, highway, name = record
        cls = road_class(highway)
        identity = str(name).strip() if name else ""
        key = (cls, identity or sha256(line.normalize().wkb).hexdigest())
        groups[key].append(index)
    ordered = []
    for (cls, identity), indices in groups.items():
        geometry = unary_union([records[i][0] for i in indices])
        protected = cls in {"motorways", "main"} or geometry.length < 4.0 or (
            route_region is not None and geometry.distance(route_region) <= 2.0
        )
        ordered.append((not protected, list(ROAD_CLASSES).index(cls), -geometry.length,
                        geometry.normalize().wkb, identity, indices, geometry, cls))
    occupied = []
    cells = defaultdict(set)
    cell_size = max(4., spacing_mm * 4)

    def cell_keys(bounds):
        left, bottom, right, top = bounds
        for x in range(floor(left / cell_size), floor(right / cell_size) + 1):
            for y in range(floor(bottom / cell_size), floor(top / cell_size) + 1):
                yield x, y

    selected = set()
    dispositions = []
    for unprotected, _, _, _, identity, indices, geometry, cls in sorted(ordered):
        candidates = set()
        for key in cell_keys(geometry.bounds):
            candidates.update(cells.get(key, ()))
        nearby = [occupied[i] for i in sorted(candidates) if occupied[i].intersects(geometry)]
        covered = geometry.intersection(unary_union(nearby)).length if nearby else 0.
        ratio = covered / geometry.length if geometry.length else 0.
        retain = not unprotected or ratio < .85
        if retain:
            selected.update(indices)
            corridor = geometry.buffer(widths[cls] / 2 + spacing_mm)
            position = len(occupied)
            occupied.append(corridor)
            for key in cell_keys(corridor.bounds):
                cells[key].add(position)
        dispositions.append({
            "class": cls, "identity": identity,
            "geometry_sha256": sha256(geometry.normalize().wkb).hexdigest(),
            "source_count": len(indices), "retained": retain,
            "source_geometry_sha256": sorted(sha256(records[i][0].normalize().wkb).hexdigest()
                                               for i in indices),
            "reason": "protected_corridor" if not unprotected else
                      "distinct_corridor" if retain else "print_space_redundancy",
            "covered_length_ratio": ratio,
        })
    return selected, {"algorithm": ALGORITHM, "spacing_mm": spacing_mm,
                      "source_count": len(records), "retained_count": len(selected),
                      "omitted_count": len(records) - len(selected),
                      "corridors": dispositions}


def partition_classes(polygons_by_class, frame_region, route_region=None):
    """Own intersections once, in priority order, without overlapping volumes."""
    owned = route_region.buffer(.03) if route_region is not None else box(0, 0, 0, 0)
    result = {}
    for cls in ROAD_CLASSES:
        sources = polygons_by_class.get(cls, [])
        if not sources:
            continue
        region = unary_union(sources).buffer(.01).buffer(-.01).intersection(frame_region)
        region = region.difference(owned)
        if not region.is_valid:
            raise ValueError(f"Invalid road class partition: {cls}")
        if not region.is_empty:
            result[cls] = region
            owned = unary_union([owned, region])
    return result
