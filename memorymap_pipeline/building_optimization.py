"""Opt-in, deterministic print-space decisions without inventing OSM classes."""

import logging
import math
from dataclasses import dataclass
from time import perf_counter

import numpy as np
from shapely import make_valid
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

from .building_classification import BuildingClass
from .building_generalization import has_local_core, local_core_width_mm
from .building_grouping import FootprintGroup, group_footprints
from .print_scale import PrintScaleContext
from .spatial_area import AreaMask


def polygonal(geometry):
    """Discard Boolean line/point residue; never manufacture a bounding box."""
    if geometry.is_empty:
        return Polygon()
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.geom_type == "Polygon":
        return geometry
    return unary_union(
        [
            polygonal(part)
            for part in getattr(geometry, "geoms", ())
            if part.geom_type in {"Polygon", "MultiPolygon", "GeometryCollection"}
        ]
    )


def parts(geometry):
    return [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)


class _LocalObstacles:
    """Exact union-overlap area without rebuilding a frame-wide union per source.

    Intersection distributes over union. Clip only intersecting obstacles first,
    then union those small pieces so overlapping barriers are not counted twice.
    The source index is excluded, including when checking protected enlargement.
    """

    def __init__(self, sources, fixed):
        self.sources = sources
        self.tree = STRtree(sources)
        self.fixed = [(geometry, AreaMask(geometry)) for geometry in fixed if not geometry.is_empty]

    def intersection_area(self, candidate, source_index):
        if candidate.is_empty:
            return 0.0
        pieces = [
            piece for _geometry, mask in self.fixed for piece in mask.intersections(candidate)
        ]
        pieces.extend(
            candidate.intersection(self.sources[int(j)])
            for j in self.tree.query(candidate, predicate="intersects")
            if int(j) != source_index
        )
        area = unary_union(pieces).area if pieces else 0.0
        # Artificial partition boundaries must not alter the 1e-8 decision.
        # Zero overlaps are far from that threshold and need no global overlay.
        if abs(area - 1e-8) <= 1e-9:
            exact = [candidate.intersection(geometry) for geometry, _mask in self.fixed]
            exact.extend(
                candidate.intersection(self.sources[int(j)])
                for j in self.tree.query(candidate, predicate="intersects")
                if int(j) != source_index
            )
            area = unary_union(exact).area
        return area


def simplify_footprint(polygon, context, *, area_budget=0.2, forbidden=None, forbidden_area=None):
    """Remove narrow appendages with opening, bounded by source area loss.

    Restrict to the source to avoid new neighbor/road conflicts. Retain large
    courtyard holes; return the source when a safe simplification is unavailable.
    ``forbidden_area`` lazily measures exact obstacle overlap only for added area.
    """
    if polygon.is_empty:
        return polygon
    tolerance = context.simplify_tolerance_mm
    candidate = polygonal(polygon.simplify(tolerance, preserve_topology=True).intersection(polygon))
    radius = max(0, context.robust_width_mm / 2 - 1e-4)
    opened = polygonal(
        candidate.buffer(-radius, join_style=2).buffer(radius, join_style=2).intersection(candidate)
    )
    if not opened.is_empty:
        candidate = opened
    if candidate.is_empty or candidate.area < polygon.area * (1 - area_budget):
        return polygon
    filled = polygonal(
        unary_union(
            [
                Polygon(
                    part.exterior,
                    [
                        hole
                        for hole in part.interiors
                        if Polygon(hole).area >= context.robust_width_mm**2
                    ],
                )
                for part in parts(candidate)
            ]
        )
    )
    added = filled.difference(polygon)
    if (
        added.area <= polygon.area * area_budget
        and (forbidden is None or added.intersection(forbidden).area <= 1e-8)
        and (forbidden_area is None or forbidden_area(added) <= 1e-8)
    ):
        candidate = filled
    return candidate


@dataclass
class OptimizationResult:
    footprints: list
    groups: list[FootprintGroup]
    omitted: set[int]
    decisions: list[dict]
    group_heights: dict[int, float]


def optimize_buildings(
    elements,
    heights,
    context: PrintScaleContext,
    *,
    allowed_region,
    barriers=None,
    route_exclusion=None,
    allow_grouping=True,
    max_group_height_mm=3.0,
    width_cache=None,
    progress_callback=None,
    timings=None,
    maximum_area_expansion=8.0,
    maximum_group_members=128,
    maximum_candidate_neighbors=None,
    preserve_large_footprints=False,
):
    """Keep semantics separate from physical policy, retaining source indices.

    Parts and their contacting parents are protected from independent cleanup;
    they all receive the same route cut. Roofs are generated from final outlines.
    Low-rise unknowns may group/omit, but remain UNKNOWN in every record.
    """
    if not math.isfinite(max_group_height_mm) or max_group_height_mm <= 0:
        raise ValueError("Group height must be positive and finite")
    stage_started = perf_counter()
    width_cache = {} if width_cache is None else width_cache

    def progress(fraction, message):
        if progress_callback is not None:
            progress_callback(fraction, message)

    def timed_stage(name):
        nonlocal stage_started
        elapsed = perf_counter() - stage_started
        if timings is not None:
            timings[name] = elapsed
        logging.getLogger(__name__).info("Building optimization %s: %.3fs", name, elapsed)
        stage_started = perf_counter()

    progress(0.0, "Preparing building optimization")
    original = [element[0] for element in elements]
    part_shapes = unary_union([p for p, _d, is_part, _l in elements if is_part])
    prepared_parts = prep(part_shapes)
    protected_classes = {
        BuildingClass.LANDMARK,
        BuildingClass.STADIUM_ARENA,
        BuildingClass.RELIGIOUS,
        BuildingClass.CIVIC_INSTITUTIONAL,
    }
    footprints, decisions, ordinary = [], [], set()
    omitted = set()
    width = context.robust_width_mm
    minimum_area = width**2
    route = Polygon() if route_exclusion is None else route_exclusion
    obstacles = Polygon() if barriers is None else barriers
    local_obstacles = _LocalObstacles(original, [obstacles, route])
    for i, (source, dims, is_part, landmark) in enumerate(elements):
        if i % 100 == 0:
            progress(
                0.4 * i / max(1, len(elements)),
                f"Simplifying building footprints {i}/{len(elements)}",
            )
        linked = is_part or (not part_shapes.is_empty and prepared_parts.intersects(source))
        courtyard = any(
            Polygon(hole).area >= minimum_area for part in parts(source) for hole in part.interiors
        )
        protected = (
            linked
            or courtyard
            or landmark is not None
            or dims.building_class in protected_classes
            or (preserve_large_footprints and source.area >= 4 * minimum_area)
        )
        tall = heights[i] > max_group_height_mm or (
            dims.height_source in {"height", "levels"} and dims.total_height_m > 15
        )
        clipped = polygonal(source.intersection(allowed_region).difference(route))
        decision = {
            "source_class": dims.building_class.value,
            "height_evidence": dims.height_source,
            "policy_class": "protected" if protected else "tall" if tall else "ordinary_low_rise",
            "source_area_mm2": source.area,
            "route_clipped_area_mm2": max(0.0, source.area - clipped.area),
            "action": "preserved",
            "reason": "substantial_footprint",
        }
        if clipped.is_empty:
            omitted.add(i)
            decision.update(
                action="omitted",
                reason="route_exclusion",
                output_area_mm2=0.0,
                output_core_width_mm=0.0,
            )
        elif not protected:
            simplified = simplify_footprint(
                clipped,
                context,
                forbidden_area=lambda added, source_index=i: local_obstacles.intersection_area(
                    added, source_index
                ),
            )
            if not simplified.equals(clipped):
                decision.update(action="simplified", reason="small_boundary_details")
            clipped = simplified
            if not tall and dims.min_height_m == 0:
                ordinary.add(i)
        if decision["route_clipped_area_mm2"] > 1e-8 and decision["action"] == "preserved":
            decision.update(action="simplified", reason="route_clearance")
        footprints.append(clipped)
        decisions.append(decision)

    # Empty sources remain indexed for provenance but never participate in groups.
    eligible = [
        i
        for i in ordinary
        if i not in omitted and footprints[i].geom_type == "Polygon" and not footprints[i].interiors
    ]
    timed_stage("simplification")
    progress(0.4, "Grouping building footprints")
    groups = (
        group_footprints(
            footprints,
            eligible,
            unary_union([obstacles, route]),
            width_mm=width,
            gap_mm=context.merge_gap_mm,
            span_mm=context.group_span_mm,
            maximum_area_expansion=maximum_area_expansion,
            maximum_members=maximum_group_members,
            maximum_candidate_neighbors=maximum_candidate_neighbors,
            allowed_region=allowed_region,
            progress_callback=lambda fraction, message: progress(0.4 + 0.35 * fraction, message),
        )
        if allow_grouping
        else []
    )
    timed_stage("grouping")
    progress(0.75, "Finalizing building printability")
    accepted_groups = []
    group_heights = {}
    for group in groups:
        geometry = simplify_footprint(group.geometry, context)
        # Cleanup must not lose contact with any member or leave disconnected masses.
        if (
            geometry.geom_type != "Polygon"
            or not has_local_core(geometry, width)
            or any(geometry.intersection(footprints[i]).area <= 1e-8 for i in group.members)
        ):
            geometry = group.geometry
        accepted_groups.append(FootprintGroup(group.members, geometry))
        representative = min(
            max_group_height_mm, float(np.median([heights[i] for i in group.members]))
        )
        group_heights[group.members[0]] = representative
        group_width = local_core_width_mm(geometry, cache=width_cache)
        for i in group.members:
            decisions[i].update(
                action="grouped",
                reason="bounded_neighborhood_mass",
                output_area_mm2=geometry.area,
                output_core_width_mm=group_width,
            )
    grouped = {i for g in accepted_groups for i in g.members}

    for i, polygon in enumerate(footprints):
        if i % 100 == 0:
            progress(
                0.75 + 0.25 * i / max(1, len(footprints)),
                f"Checking building printability {i}/{len(footprints)}",
            )
        if i in omitted or i in grouped:
            continue
        decision = decisions[i]
        if i in ordinary:
            # Screen every fragment, not just the largest surviving core.
            kept = [
                p for p in parts(polygon) if p.area >= minimum_area and has_local_core(p, width)
            ]
            result = polygonal(unary_union(kept))
            if result.is_empty:
                omitted.add(i)
                footprints[i] = Polygon()
                decision.update(
                    action="omitted",
                    reason="isolated_below_print_target",
                    output_area_mm2=0.0,
                    output_core_width_mm=0.0,
                )
                continue
            if not result.equals(polygon):
                decision.update(action="simplified", reason="subthreshold_fragments_removed")
            footprints[i] = polygon = result
            radius = max(0.0, width / 2 - 1e-4)
            core_shape = polygon.buffer(-radius, join_style=2).buffer(radius, join_style=2)
            if polygon.difference(core_shape).area > polygon.area * 0.05:
                decision.update(action="unresolved", reason="boundary_cleanup_exceeds_budget")
        else:
            # Only modest enlargement of simple protected footprints is allowed.
            # Linked parts/courtyards/towers remain intact and visibly unresolved.
            weak = any(not has_local_core(p, width) for p in parts(polygon))
            if weak and decision["policy_class"] == "protected" and not elements[i][2]:
                candidate = polygonal(polygon.buffer(context.xy_resolution_mm / 2, join_style=2))
                safe = (
                    polygon.geom_type == "Polygon"
                    and not polygon.interiors
                    and not polygon.intersects(part_shapes)
                    and candidate.area <= polygon.area * 1.5
                    and allowed_region.covers(candidate)
                    and local_obstacles.intersection_area(candidate, i) <= 1e-8
                    and has_local_core(candidate, width)
                )
                if safe:
                    footprints[i] = polygon = candidate
                    decision.update(action="enlarged", reason="bounded_landmark_enlargement")
                    weak = False
            aspect = heights[i] / max(local_core_width_mm(polygon, cache=width_cache), 1e-4)
            if weak or aspect > 6:
                decision.update(
                    action="unresolved",
                    reason="protected_or_tall_printability",
                    height_to_core_width_ratio=aspect,
                )
        decision.update(
            output_area_mm2=polygon.area,
            output_core_width_mm=local_core_width_mm(polygon, cache=width_cache),
        )
    timed_stage("printability")
    progress(1.0, "Building optimization complete")
    return OptimizationResult(footprints, accepted_groups, omitted, decisions, group_heights)
