"""Versioned illustrative city relief in physical print space.

Source classifications are never inferred from neighborhood appearance.
"""

from dataclasses import replace

import numpy as np
from shapely import affinity
from shapely.geometry import LineString
from shapely.ops import split, unary_union
from shapely.strtree import STRtree

from .building_generalization import has_local_core

PRESET_VERSION = "city-relief-v1"
DETAIL_GAPS = {"sparse": 1.5, "balanced": 1.0, "dense": 0.65}
DETAIL_WIDTHS = {"sparse": 3.0, "balanced": 2.5, "dense": 2.125}
MAJOR_ROADS = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
}


def validate(config):
    if config.get("city_relief_preset_version", PRESET_VERSION) != PRESET_VERSION:
        raise ValueError("Unsupported City relief preset version")
    if config.get("building_appearance_mode", "existing") not in {"existing", "city_relief"}:
        raise ValueError("Unsupported building appearance mode")
    if config.get("city_relief_detail", "balanced") not in DETAIL_GAPS:
        raise ValueError("Unsupported City relief neighborhood detail")


def configure(config):
    validate(config)
    if config.get("building_appearance_mode", "existing") != "city_relief":
        return config
    config = dict(config)
    resolution = float(config.get("line_width_mm") or config.get("nozzle_diameter_mm", 0.4))
    widths = dict(config["road_widths"])
    for kind, width in widths.items():
        widths[kind] = max(resolution, min(width, 0.8 if kind in MAJOR_ROADS else resolution))
    types = [
        kind
        for kind in config["road_types"]
        if kind not in {"service", "cycleway", "path", "footway", "track", "steps", "pedestrian"}
    ]
    config.update(
        building_generalization_mode="print_optimized",
        building_height_mode="architectural_relief",
        city_relief_preset_version=PRESET_VERSION,
        road_widths=widths,
        landscape_road_widths=widths,
        road_types=types,
        landscape_road_types=types,
        road_height=0.6,
        landscape_road_height=0.6,
    )
    return config


def height(dimensions, *, landmark=False, source_height_m=None):
    h = dimensions.total_height_m if source_height_m is None else source_height_m
    if landmark:
        return min(16.0, max(6.0, 6.0 + max(0.0, h - 15.0) / 20.0))
    if h <= 9 and dimensions.building_type in {"shed", "garage", "garages", "carport", "hut"}:
        return 1.2
    if h <= 9:
        return 1.6 + 0.8 * max(0.0, h - 3.0) / 6.0
    if h <= 30:
        return 3.0 + 3.0 * (h - 9.0) / 21.0
    return min(12.0, 6.0 + 6.0 * (h - 30.0) / 170.0)


def context_for(context, detail):
    return replace(
        context,
        robust_width_mm=DETAIL_WIDTHS[detail] * context.xy_resolution_mm,
        merge_gap_mm=context.xy_resolution_mm * DETAIL_GAPS[detail],
        group_span_mm=10.0 * context.xy_resolution_mm,
    )


def typify_neighborhoods(
    result,
    elements,
    heights,
    context,
    allowed,
    barriers,
    route,
    minimum_width,
    progress_callback=None,
):
    """Place bounded, source-oriented representatives without an unbounded merge search."""
    from shapely.geometry import Polygon
    from shapely.prepared import prep
    from .building_grouping import FootprintGroup
    from .spatial_area import AreaMask

    sources = [
        e[0].intersection(allowed).difference(route)
        if route is not None
        else e[0].intersection(allowed)
        for e in elements
    ]
    eligible = {
        i
        for i in result.omitted
        if result.decisions[i]["reason"] == "isolated_below_print_target"
        and sources[i].geom_type == "Polygon"
        and not sources[i].interiors
    }
    tree = STRtree(sources)
    fixed = unary_union(
        [g for g in (barriers, route) if g is not None]
        + [p for i, p in enumerate(result.footprints) if i not in result.omitted]
    )
    prepared = prep(fixed)
    mask = AreaMask(fixed)
    accepted = []
    accepted_members = []
    accepted_tree = STRtree(accepted)
    for position, seed in enumerate(sorted(eligible)):
        if progress_callback is not None and position % 100 == 0:
            progress_callback(
                position / max(1, len(eligible)),
                f"Typifying neighborhoods {position}/{len(eligible)}",
            )
        if seed not in result.omitted:
            continue
        source = sources[seed]
        coords = np.asarray(source.minimum_rotated_rectangle.exterior.coords)
        edges = np.diff(coords, axis=0)
        direction = edges[int(np.argmax(np.linalg.norm(edges, axis=1)))]
        direction = direction / np.linalg.norm(direction)
        normal = np.array([-direction[1], direction[0]])
        center = np.asarray(source.centroid.coords[0])
        found = False
        for width in dict.fromkeys((context.robust_width_mm, minimum_width)):
            for dx, dy in (
                (0, 0),
                (-0.2, 0),
                (0.2, 0),
                (0, -0.2),
                (0, 0.2),
                (-0.2, -0.2),
                (0.2, 0.2),
                (-0.2, 0.2),
                (0.2, -0.2),
            ):
                origin = center + width * (dx * direction + dy * normal)
                candidate = Polygon(
                    [
                        origin + width * (x * direction + y * normal)
                        for x, y in ((-0.6, -0.5), (0.6, -0.5), (0.6, 0.5), (-0.6, 0.5))
                    ]
                )
                if not allowed.covers(candidate) or (
                    mask.exceeds(candidate, 1e-8) and prepared.intersects(candidate)
                ):
                    continue
                if any(
                    candidate.distance(accepted[int(j)]) < context.merge_gap_mm - 1e-8
                    for j in accepted_tree.query(candidate.buffer(context.merge_gap_mm))
                ):
                    continue
                touched = {
                    int(j)
                    for j in tree.query(candidate, predicate="intersects")
                    if candidate.intersection(sources[int(j)]).area > 1e-8
                }
                if seed not in touched or not touched <= eligible or not touched <= result.omitted:
                    continue
                # Every source must belong to this occupied neighborhood; no bridges across open space.
                connected = {seed}
                pending = touched - connected
                while pending:
                    next_members = {
                        i
                        for i in pending
                        if any(
                            sources[i].distance(sources[j]) <= context.merge_gap_mm
                            for j in connected
                        )
                    }
                    if not next_members:
                        break
                    connected.update(next_members)
                    pending.difference_update(next_members)
                if pending:
                    continue
                occupied = unary_union([sources[i] for i in touched])
                x0, y0, x1, y1 = occupied.bounds
                if max(x1 - x0, y1 - y0) > context.group_span_mm:
                    continue
                if not occupied.buffer(width * 0.75).covers(candidate):
                    continue
                members = tuple(sorted(touched))
                if len(members) > 1:
                    result.groups.append(FootprintGroup(members, candidate))
                    result.group_heights[members[0]] = float(
                        np.median([heights[i] for i in members])
                    )
                else:
                    result.footprints[seed] = candidate
                for i in members:
                    result.omitted.remove(i)
                    result.decisions[i].update(
                        action="typified",
                        reason="bounded_neighborhood_representative",
                        representative_source_indices=list(members),
                        output_area_mm2=candidate.area,
                        output_core_width_mm=width,
                        preferred_width_unmet=width < context.robust_width_mm,
                    )
                accepted.append(candidate)
                accepted_members.append(members)
                accepted_tree = STRtree(accepted)
                found = True
                break
            if found:
                break
        if not found:
            result.decisions[seed]["reason"] = "no_safe_city_representative"
    # Crowded neighborhoods may need one wider, connected stepped mass.
    for seed in sorted(eligible & result.omitted):
        source = sources[seed]
        neighbors = sorted(
            (int(j) for j in accepted_tree.query(source.buffer(context.merge_gap_mm))),
            key=lambda j: (source.distance(accepted[j]), j),
        )[:4]
        for neighbor in neighbors:
            old_members = accepted_members[neighbor]
            candidate = accepted[neighbor].union(source).convex_hull.minimum_rotated_rectangle
            if not allowed.covers(candidate) or (
                mask.exceeds(candidate, 1e-8) and prepared.intersects(candidate)
            ):
                continue
            if any(
                j != neighbor and candidate.distance(accepted[j]) < context.merge_gap_mm - 1e-8
                for j in (
                    int(j) for j in accepted_tree.query(candidate.buffer(context.merge_gap_mm))
                )
            ):
                continue
            touched = {
                int(j)
                for j in tree.query(candidate, predicate="intersects")
                if candidate.intersection(sources[int(j)]).area > 1e-8
            }
            if (
                seed not in touched
                or not touched <= eligible
                or not touched <= result.omitted | set(old_members)
            ):
                continue
            members = tuple(sorted(touched | set(old_members)))
            connected = set(old_members)
            pending = set(members) - connected
            while pending:
                connected_next = {
                    i
                    for i in pending
                    if any(
                        sources[i].distance(sources[j]) <= context.merge_gap_mm for j in connected
                    )
                }
                if not connected_next:
                    break
                connected.update(connected_next)
                pending.difference_update(connected_next)
            if pending:
                continue
            occupied = unary_union([sources[i] for i in members])
            x0, y0, x1, y1 = occupied.bounds
            if max(x1 - x0, y1 - y0) > context.group_span_mm or not occupied.buffer(
                context.robust_width_mm * 0.75
            ).covers(candidate):
                continue
            result.groups = [g for g in result.groups if g.members != old_members]
            result.groups.append(FootprintGroup(members, candidate))
            result.group_heights[members[0]] = float(np.median([heights[i] for i in members]))
            for i in members:
                result.omitted.discard(i)
                result.decisions[i].update(
                    action="typified",
                    reason="connected_stepped_neighborhood_fallback",
                    connected_massing=True,
                    representative_source_indices=list(members),
                    output_area_mm2=candidate.area,
                    output_core_width_mm=minimum_width,
                )
            accepted[neighbor] = candidate
            accepted_members[neighbor] = members
            accepted_tree = STRtree(accepted)
            break
    return result


def rescue_groups(result, elements, heights, context, allowed, barriers, route, minimum_width):
    return typify_neighborhoods(
        result, elements, heights, context, allowed, barriers, route, minimum_width
    )


def rescue_singletons(result, elements, context, allowed, barriers, route):
    """Enlarge isolated ordinary sources only inside a bounded occupied envelope."""
    sources = [e[0] for e in elements]
    tree = STRtree(sources)
    fixed = unary_union([g for g in (barriers, route) if g is not None])
    accepted = [g.geometry for g in result.groups]
    accepted_tree = STRtree(accepted)
    for i in sorted(result.omitted):
        decision = result.decisions[i]
        if decision["reason"] != "isolated_below_print_target":
            continue
        source = sources[i]
        if source.geom_type != "Polygon" or source.interiors:
            continue
        rectangle = source.minimum_rotated_rectangle
        if rectangle.is_empty:
            continue
        coords = np.asarray(rectangle.exterior.coords)
        edges = np.diff(coords, axis=0)
        short = float(np.min(np.linalg.norm(edges, axis=1)))
        if short <= 0:
            continue
        candidate = affinity.scale(
            rectangle,
            xfact=max(1.0, context.robust_width_mm / short),
            yfact=max(1.0, context.robust_width_mm / short),
            origin=source.centroid,
        )
        # Expansion is local and cannot cross geographic or source geometry barriers.
        safe = (
            source.buffer(context.robust_width_mm * 0.75).covers(candidate)
            and allowed.covers(candidate)
            and candidate.intersection(fixed).area <= 1e-8
            and all(
                candidate.intersection(sources[int(j)]).area <= 1e-8
                for j in tree.query(candidate, predicate="intersects")
                if int(j) != i
            )
            and all(
                candidate.intersection(accepted[int(j)]).area <= 1e-8
                for j in accepted_tree.query(candidate, predicate="intersects")
            )
        )
        if safe and has_local_core(candidate, context.robust_width_mm):
            result.footprints[i] = candidate
            result.omitted.remove(i)
            decision.update(
                action="typified",
                reason="bounded_individual_representative",
                output_area_mm2=candidate.area,
                representative_source_indices=[i],
                output_core_width_mm=context.robust_width_mm,
            )
            accepted.append(candidate)
            accepted_tree = STRtree(accepted)
        else:
            decision["reason"] = "no_safe_city_representative"
    return result


def stepped_parts(polygon, members, elements, heights, minimum_width):
    """Two disjoint height regions; source evidence chooses each visible top."""
    if len(members) < 2:
        return [(polygon, float(np.median([heights[i] for i in members])))]
    rect = polygon.minimum_rotated_rectangle
    coords = np.asarray(rect.exterior.coords)
    edges = np.diff(coords, axis=0)
    axis = edges[int(np.argmax(np.linalg.norm(edges, axis=1)))]
    axis = axis / np.linalg.norm(axis)
    normal = np.array([-axis[1], axis[0]])
    center = np.asarray(polygon.centroid.coords[0])
    cut = LineString([center - normal * 1000, center + normal * 1000])
    pieces = list(split(polygon, cut).geoms)
    if len(pieces) != 2 or any(not has_local_core(p, minimum_width) for p in pieces):
        return [(polygon, float(np.median([heights[i] for i in members])))]
    result = []
    for piece in pieces:
        values = [heights[i] for i in members if piece.intersection(elements[i][0]).area > 1e-8]
        result.append((piece, float(np.median(values or [heights[i] for i in members]))))
    return result


def enlarge_landmarks(elements, roots, allowed, barriers, route):
    """Scale complete registered assemblies, falling back safely to the source."""
    sources = [e[0] for e in elements]
    tree = STRtree(sources)
    fixed = unary_union([g for g in (barriers, route) if g is not None])
    from .spatial_area import AreaMask

    fixed_mask = AreaMask(fixed)
    assemblies = {}
    for i, root in enumerate(roots):
        assemblies.setdefault(root, []).append(i)
    output = list(elements)
    reports = {}
    accepted = []
    accepted_tree = STRtree([])
    indexed_count = 0
    for root, members in sorted(assemblies.items()):
        from .building_classification import BuildingClass

        if not any(
            elements[i][3] is not None
            or elements[i][1].building_class
            in {
                BuildingClass.LANDMARK,
                BuildingClass.STADIUM_ARENA,
                BuildingClass.CIVIC_INSTITUTIONAL,
                BuildingClass.RELIGIOUS,
            }
            or (
                elements[i][1].height_source in {"height", "levels"}
                and elements[i][1].total_height_m > 30
            )
            for i in members
        ):
            continue
        envelope = unary_union([sources[i] for i in members])
        origin = envelope.centroid
        scale = 1.0
        for factor in (1.5, 1.25, 1.1):
            candidate = affinity.scale(envelope, xfact=factor, yfact=factor, origin=origin)
            added = candidate.difference(envelope)
            if (
                allowed.covers(candidate)
                and not fixed_mask.exceeds(added, 1e-8)
                and all(
                    added.intersection(sources[int(j)]).area <= 1e-8
                    for j in tree.query(candidate, predicate="intersects")
                    if int(j) not in members
                )
                and all(
                    added.intersection(accepted[int(j)]).area <= 1e-8
                    for j in accepted_tree.query(candidate)
                )
                and all(added.intersection(g).area <= 1e-8 for g in accepted[indexed_count:])
            ):
                scale = factor
                break
        for i in members:
            polygon, dims, part, landmark = elements[i]
            output[i] = (
                affinity.scale(polygon, xfact=scale, yfact=scale, origin=origin),
                dims,
                part,
                landmark,
            )
            reports[i] = {
                "requested_footprint_scale": 1.5,
                "effective_footprint_scale": scale,
                "assembly_root": root,
                "reason": "bounded_enlargement" if scale > 1 else "no_safe_enlargement",
            }
        accepted.append(unary_union([output[i][0] for i in members]))
        if len(accepted) - indexed_count >= 64:
            accepted_tree = STRtree(accepted)
            indexed_count = len(accepted)
    return output, reports


def open_space_polygons(bbox, transform, center_lat, center_lon, source=None):
    """Acquire explicit park/garden boundaries; unknown acquisition is an error."""
    from .building_download import download_tiles
    from .buildings import OVERPASS_ENDPOINTS, _overpass_geometry, _transform_shapely_polygon

    if source is not None:
        return list(source)
    elements = download_tiles(
        bbox, OVERPASS_ENDPOINTS, tags=(("leisure", "^(park|garden|nature_reserve)$"),)
    )
    result = []
    for e in elements:
        # Multipolygon relations can carry descriptive point members as well
        # as boundary ways. These points do not define occupied open space.
        boundary = e
        if e.get("type") == "relation":
            boundary = dict(
                e,
                members=[
                    member
                    for member in e.get("members", [])
                    if not (
                        member.get("type") == "node" and member.get("role") in {"artwork", "label"}
                    )
                ],
            )
        poly = _overpass_geometry(boundary)
        if poly is None:
            raise RuntimeError(
                f"Open-space geometry could not be decoded: {e.get('type')} {e.get('id')}"
            )
        if poly is not None:
            result.append(
                _transform_shapely_polygon(
                    poly, center_lat=center_lat, center_lon=center_lon, transform=transform
                )
            )
    return result
