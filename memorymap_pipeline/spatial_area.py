"""Spatially partition a fixed area mask for repeated small overlap queries."""

from shapely import get_num_coordinates
from shapely.geometry import box
from shapely.strtree import STRtree


class AreaMask:
    """Disjoint interiors preserve overlap area without traversing a whole map.

    Split using exact Boolean intersections (never simplified or rounded masks).
    Near the decision threshold, use the original mask to avoid letting arithmetic
    at artificial tile boundaries change the caller's clearance decision.
    """

    def __init__(self, geometry, *, maximum_coordinates=2048):
        self.geometry = geometry
        self.reference_areas = {}
        pieces = []

        def partition(part, depth=0):
            if part.is_empty:
                return
            if get_num_coordinates(part) <= maximum_coordinates or depth >= 16:
                pieces.append(part)
                return
            x0, y0, x1, y1 = part.bounds
            if x1 - x0 >= y1 - y0:
                middle = (x0 + x1) / 2
                bounds = ((x0, y0, middle, y1), (middle, y0, x1, y1))
            else:
                middle = (y0 + y1) / 2
                bounds = ((x0, y0, x1, middle), (x0, middle, x1, y1))
            for extent in bounds:
                partition(part.intersection(box(*extent)), depth + 1)

        partition(geometry)
        self.pieces = pieces
        self.tree = STRtree(pieces)

    def exceeds(self, polygon, threshold):
        indices = self.tree.query(polygon, predicate="intersects")
        if not len(indices):
            return 0.0 > threshold
        area = sum(polygon.intersection(self.pieces[int(i)]).area for i in indices)
        if abs(area - threshold) <= max(1e-9, abs(threshold) * 1e-6):
            key = polygon.wkb
            if key not in self.reference_areas:
                if len(self.reference_areas) >= 65536:
                    del self.reference_areas[next(iter(self.reference_areas))]
                self.reference_areas[key] = polygon.intersection(self.geometry).area
            area = self.reference_areas[key]
        return area > threshold
