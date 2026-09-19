"""Conservative print-space grouping of small, low building footprints."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree


@dataclass(frozen=True)
class FootprintGroup:
    members: tuple[int, ...]
    geometry: Polygon


def group_footprints(
    footprints,
    eligible,
    barriers=None,
    *,
    width_mm=0.8,
    gap_mm=0.4,
    span_mm=4.0,
    maximum_members=32,
    allowed_region=None,
):
    """Grow bounded neighborhood masses; never add area across a known barrier.

    Existing courtyards, tall buildings and landmarks must be excluded by the
    caller. Unresolved singletons are retained, never deleted or enlarged here.
    """
    if not all(math.isfinite(v) and v > 0 for v in (width_mm, gap_mm, span_mm)):
        raise ValueError("Grouping dimensions must be positive and finite")
    eligible = set(eligible)
    if not eligible:
        return []
    obstacles = [p for i, p in enumerate(footprints) if i not in eligible]
    if barriers is not None and not barriers.is_empty:
        obstacles.append(barriers)
    protected = unary_union(obstacles)
    prepared = prep(protected)
    accepted_tree = None
    accepted_shapes = []

    def blocked(polygon):
        if allowed_region is not None and not allowed_region.covers(polygon):
            return True
        if prepared.intersects(polygon) and polygon.intersection(protected).area > 1e-10:
            return True
        return accepted_tree is not None and any(
            polygon.intersection(accepted_shapes[int(i)]).area > 1e-10
            for i in accepted_tree.query(polygon)
        )

    available = {i for i in eligible if not blocked(footprints[i])}
    tree = STRtree(footprints)
    radius = width_mm / 2

    def substantial(polygon):
        return not polygon.buffer(-radius, join_style=2).is_empty

    def finish_mass(hull, source_area, members):
        # Fill only the minimum surrounding space needed for the width target.
        # This remains bounded by street/water/route and protected-building masks.
        mass = hull
        if not substantial(mass):
            low, high = 0.0, radius + 1e-4
            for _ in range(14):
                middle = (low + high) / 2
                if substantial(hull.buffer(middle, join_style=2)):
                    high = middle
                else:
                    low = middle
            mass = hull.buffer(high, join_style=2)
        screen_radius = max(radius - 1e-4, radius * 0.999)
        core = mass.buffer(-screen_radius, join_style=2)
        opened = core.buffer(screen_radius, join_style=2).intersection(mass)
        if opened.area < mass.area * (1 - 1e-5):
            # Acute hull tips can disappear despite a broad central core.
            mass = mass.minimum_rotated_rectangle
        x0, y0, x1, y1 = mass.bounds
        if max(x1 - x0, y1 - y0) > span_mm or mass.area > 8 * source_area or blocked(mass):
            return None
        # Separate overlapping shells can cancel in a slicer even when each
        # shell is closed. Keep growing until every touched source is a member.
        if any(
            int(i) not in members and mass.intersection(footprints[int(i)]).area > 1e-10
            for i in tree.query(mass)
        ):
            return None
        return mass if substantial(mass) else None

    def order(i):
        p = footprints[i]
        return (p.area, *p.bounds, p.wkb_hex)

    groups = []
    failed_seeds = set()
    for seed in sorted(available, key=order):
        if (
            seed not in available
            or seed in failed_seeds
            or substantial(footprints[seed])
            or blocked(footprints[seed])
        ):
            continue
        members = [seed]
        hull = footprints[seed]
        source_area = hull.area
        while len(members) < maximum_members:
            options = []
            for candidate in tree.query(hull.buffer(gap_mm)):
                i = int(candidate)
                if i not in available or i in members:
                    continue
                distance = min(footprints[j].distance(footprints[i]) for j in members)
                if distance > gap_mm:
                    continue
                candidate_hull = hull.union(footprints[i]).convex_hull
                x0, y0, x1, y1 = candidate_hull.bounds
                if max(x1 - x0, y1 - y0) > span_mm:
                    continue
                if candidate_hull.area > 8 * (source_area + footprints[i].area):
                    continue
                if blocked(candidate_hull):
                    continue
                options.append((distance, candidate_hull.area, order(i), i, candidate_hull))
            if not options:
                break
            _, _, _, i, hull = min(options, key=lambda item: item[:3])
            members.append(i)
            source_area += footprints[i].area
            mass = finish_mass(hull, source_area, members)
            if mass is not None:
                groups.append(FootprintGroup(tuple(sorted(members)), mass))
                available.difference_update(members)
                accepted_shapes.append(mass)
                accepted_tree = STRtree(accepted_shapes)
                break
        else:
            failed_seeds.update(members)
        if seed in available:
            failed_seeds.update(members)
    return groups
