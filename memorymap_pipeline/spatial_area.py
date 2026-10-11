"""Spatially partition a fixed area mask for repeated small overlap queries."""

from shapely import get_num_coordinates
from shapely.geometry import box
from shapely.prepared import prep
from shapely.strtree import STRtree


class AreaMask:
    """Disjoint interiors preserve overlap area without traversing a whole map.

    Split using exact Boolean intersections (never simplified or rounded masks).
    Near the decision threshold, use the original mask to avoid letting arithmetic
    at artificial tile boundaries change the caller's clearance decision.
    """

    def __init__(self, geometry, *, maximum_coordinates=2048, short_circuit=False):
        self.geometry = geometry
        self.short_circuit = short_circuit
        self.prepared = prep(geometry)
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
        area = 0.0
        margin = max(1e-9, abs(threshold) * 1e-6)
        for i in indices:
            area += polygon.intersection(self.pieces[int(i)]).area
            # Disjoint partition interiors contribute nonnegative area. Once
            # safely above the original decision's numerical fallback band,
            # further overlays cannot change rejection. Default callers retain
            # the original summation behavior.
            if self.short_circuit and area > threshold + margin:
                return True
        if abs(area - threshold) <= max(1e-9, abs(threshold) * 1e-6):
            key = polygon.wkb
            if key not in self.reference_areas:
                if len(self.reference_areas) >= 65536:
                    del self.reference_areas[next(iter(self.reference_areas))]
                # Exact topology establishes zero area for disjoint interiors.
                # Avoid a full-map overlay for touching road/source boundaries.
                self.reference_areas[key] = (
                    0.0
                    if not self.prepared.intersects(polygon) or self.prepared.touches(polygon)
                    else polygon.intersection(self.geometry).area
                )
            area = self.reference_areas[key]
        return area > threshold

    def intersections(self, polygon):
        """Return exact local clips; shared tile boundaries have zero area."""
        return [
            polygon.intersection(self.pieces[int(i)])
            for i in self.tree.query(polygon, predicate="intersects")
        ]
