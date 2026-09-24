"""Opt-in, deterministic print-space decisions without inventing OSM classes."""

import math
from dataclasses import dataclass

import numpy as np
from shapely import make_valid
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .building_classification import BuildingClass
from .building_generalization import has_local_core, local_core_width_mm
from .building_grouping import FootprintGroup, group_footprints
from .print_scale import PrintScaleContext


def polygonal(geometry):
    """Discard Boolean line/point residue; never manufacture a bounding box."""
    if geometry.is_empty:
        return Polygon()
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.geom_type == "Polygon":
        return geometry
    return unary_union([
        polygonal(part) for part in getattr(geometry, "geoms", ())
        if part.geom_type in {"Polygon", "MultiPolygon", "GeometryCollection"}
    ])


def parts(geometry):
    return [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)


def simplify_footprint(polygon, context, *, area_budget=0.2, forbidden=None):
    """Remove narrow appendages with opening, bounded by source area loss.

    Restrict to the source to avoid new neighbor/road conflicts. Retain large
    courtyard holes; return the source when a safe simplification is unavailable.
    """
    if polygon.is_empty:
        return polygon
    tolerance = context.simplify_tolerance_mm
    candidate = polygonal(polygon.simplify(tolerance, preserve_topology=True).intersection(polygon))
    radius = max(0, context.robust_width_mm / 2 - 1e-4)
    opened = polygonal(candidate.buffer(-radius, join_style=2).buffer(radius, join_style=2).intersection(candidate))
    if not opened.is_empty:
        candidate = opened
    if candidate.is_empty or candidate.area < polygon.area * (1 - area_budget):
        return polygon
    filled = polygonal(unary_union([
        Polygon(part.exterior, [hole for hole in part.interiors
                                if Polygon(hole).area >= context.robust_width_mm ** 2])
        for part in parts(candidate)
    ]))
    added = filled.difference(polygon)
    if (added.area <= polygon.area * area_budget
            and (forbidden is None or added.intersection(forbidden).area <= 1e-8)):
        candidate = filled
    return candidate


@dataclass
class OptimizationResult:
    footprints: list
    groups: list[FootprintGroup]
    omitted: set[int]
    decisions: list[dict]
    group_heights: dict[int, float]


def optimize_buildings(elements, heights, context: PrintScaleContext, *,
                       allowed_region, barriers=None, route_exclusion=None,
                       allow_grouping=True, max_group_height_mm=3.0):
    """Keep semantics separate from physical policy, retaining source indices.

    Parts and their contacting parents are protected from independent cleanup;
    they all receive the same route cut. Roofs are generated from final outlines.
    Low-rise unknowns may group/omit, but remain UNKNOWN in every record.
    """
    if not math.isfinite(max_group_height_mm) or max_group_height_mm <= 0:
        raise ValueError("Group height must be positive and finite")
    original = [element[0] for element in elements]
    source_tree = STRtree(original)
    part_shapes = unary_union([p for p, _d, is_part, _l in elements if is_part])
    protected_classes = {
        BuildingClass.LANDMARK, BuildingClass.STADIUM_ARENA,
        BuildingClass.RELIGIOUS, BuildingClass.CIVIC_INSTITUTIONAL,
    }
    footprints, decisions, ordinary = [], [], set()
    omitted = set()
    width = context.robust_width_mm
    minimum_area = width ** 2
    route = Polygon() if route_exclusion is None else route_exclusion
    obstacles = Polygon() if barriers is None else barriers
    for i, (source, dims, is_part, landmark) in enumerate(elements):
        linked = is_part or (not part_shapes.is_empty and source.intersects(part_shapes))
        courtyard = any(Polygon(hole).area >= minimum_area
                        for part in parts(source) for hole in part.interiors)
        protected = linked or courtyard or landmark is not None or dims.building_class in protected_classes
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
            "action": "preserved", "reason": "substantial_footprint",
        }
        if clipped.is_empty:
            omitted.add(i)
            decision.update(action="omitted", reason="route_exclusion",
                            output_area_mm2=0.0, output_core_width_mm=0.0)
        elif not protected:
            nearby = [original[int(j)] for j in source_tree.query(clipped)
                      if int(j) != i]
            simplified = simplify_footprint(
                clipped, context, forbidden=unary_union([obstacles, route, *nearby])
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
    eligible = [i for i in ordinary if i not in omitted
                and footprints[i].geom_type == "Polygon" and not footprints[i].interiors]
    groups = group_footprints(
        footprints, eligible, unary_union([obstacles, route]),
        width_mm=width, gap_mm=context.merge_gap_mm, span_mm=context.group_span_mm,
        allowed_region=allowed_region,
    ) if allow_grouping else []
    accepted_groups = []
    group_heights = {}
    for group in groups:
        geometry = simplify_footprint(group.geometry, context)
        # Cleanup must not lose contact with any member or leave disconnected masses.
        if (geometry.geom_type != "Polygon" or not has_local_core(geometry, width)
                or any(geometry.intersection(footprints[i]).area <= 1e-8 for i in group.members)):
            geometry = group.geometry
        accepted_groups.append(FootprintGroup(group.members, geometry))
        representative = min(max_group_height_mm, float(np.median([heights[i] for i in group.members])))
        group_heights[group.members[0]] = representative
        for i in group.members:
            decisions[i].update(action="grouped", reason="bounded_neighborhood_mass",
                                output_area_mm2=geometry.area, output_core_width_mm=local_core_width_mm(geometry))
    grouped = {i for g in accepted_groups for i in g.members}

    for i, polygon in enumerate(footprints):
        if i in omitted or i in grouped:
            continue
        decision = decisions[i]
        if i in ordinary:
            # Screen every fragment, not just the largest surviving core.
            kept = [p for p in parts(polygon)
                    if p.area >= minimum_area and has_local_core(p, width)]
            result = polygonal(unary_union(kept))
            if result.is_empty:
                omitted.add(i)
                footprints[i] = Polygon()
                decision.update(action="omitted", reason="isolated_below_print_target",
                                output_area_mm2=0.0, output_core_width_mm=0.0)
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
                neighbors = [original[int(j)] for j in source_tree.query(candidate)
                             if int(j) != i]
                forbidden = unary_union([obstacles, route, *neighbors])
                safe = (polygon.geom_type == "Polygon" and not polygon.interiors
                        and not polygon.intersects(part_shapes)
                        and candidate.area <= polygon.area * 1.5
                        and allowed_region.covers(candidate)
                        and candidate.intersection(forbidden).area <= 1e-8
                        and has_local_core(candidate, width))
                if safe:
                    footprints[i] = polygon = candidate
                    decision.update(action="enlarged", reason="bounded_landmark_enlargement")
                    weak = False
            aspect = heights[i] / max(local_core_width_mm(polygon), 1e-4)
            if weak or aspect > 6:
                decision.update(action="unresolved", reason="protected_or_tall_printability",
                                height_to_core_width_ratio=aspect)
        decision.update(output_area_mm2=polygon.area, output_core_width_mm=local_core_width_mm(polygon))
    return OptimizationResult(footprints, accepted_groups, omitted, decisions, group_heights)
