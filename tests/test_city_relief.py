import json

import numpy as np
import pytest
from shapely.geometry import box

from memorymap_pipeline import city_relief as c
from memorymap_pipeline.building_classification import BuildingClass
from memorymap_pipeline.building_optimization import optimize_buildings
from memorymap_pipeline.buildings import BuildingDimensions
from memorymap_pipeline.desktop.project import DesktopProject
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.print_scale import PrintScaleContext


def element(p, h=6):
    return (
        p,
        BuildingDimensions(0, h, 0, "flat", "along", BuildingClass.UNKNOWN, "height"),
        False,
        None,
    )


def context():
    return PrintScaleContext.from_frame(MapFrame(0, 0, 100, 100, 100, 100), {"line_width_mm": 0.4})


def test_height_bands_monotone_and_physical():
    heights = [c.height(element(box(0, 0, 1, 1), h)[1]) for h in (3, 6, 9, 12, 30, 60, 200, 400)]
    assert heights == sorted(heights)
    assert heights[:3] == pytest.approx([1.6, 2, 2.4])
    assert heights[-1] == 12


def test_small_isolated_source_is_represented_without_reclassification():
    ctx = c.context_for(context(), "balanced")
    elements = [element(box(4, 4, 4.1, 4.1))]
    result = optimize_buildings(elements, [2], ctx, allowed_region=box(0, 0, 20, 20))
    result = c.rescue_singletons(result, elements, ctx, box(0, 0, 20, 20), None, None)
    assert not result.omitted
    assert result.decisions[0]["action"] == "typified"
    assert result.decisions[0]["source_class"] == "unknown"
    assert result.footprints[0].area == pytest.approx(1)


@pytest.mark.parametrize("obstacle", [box(4.2, 0, 5, 10), box(0, 4.2, 10, 5)])
def test_representatives_never_cross_barriers(obstacle):
    ctx = c.context_for(context(), "balanced")
    elements = [element(box(4, 4, 4.1, 4.1))]
    result = optimize_buildings(
        elements, [2], ctx, allowed_region=box(0, 0, 20, 20), barriers=obstacle
    )
    result = c.rescue_singletons(result, elements, ctx, box(0, 0, 20, 20), obstacle, None)
    assert result.omitted == {0}
    assert result.decisions[0]["reason"] == "no_safe_city_representative"


def test_steps_preserve_evidence_height_difference():
    elems = [element(box(0, 0, 2, 2), 3), element(box(2, 0, 4, 2), 9)]
    pieces = c.stepped_parts(box(0, 0, 4, 2), [0, 1], elems, [1.6, 2.4], 0.8)
    assert sum(p.area for p, h in pieces) == pytest.approx(8)
    # Areas, not mere boundary contact, determine each side's source evidence.
    assert sorted(h for p, h in pieces) == pytest.approx([1.6, 2.4])


def test_project_v4_and_v3_migration():
    project = DesktopProject(
        MapFrame(0, 0, 100, 100, 100, 100),
        building_appearance_mode="city_relief",
        city_relief_detail="dense",
        color_preset="urban-classic",
    )
    assert DesktopProject.from_json(project.to_json()) == project
    old = project.to_dict()
    old["version"] = 3
    assert DesktopProject.from_dict(old).building_appearance_mode == "existing"


def test_invalid_appearance_rejected():
    with pytest.raises(ValueError):
        c.validate({"building_appearance_mode": "guess"})
    with pytest.raises(ValueError):
        c.validate({"city_relief_detail": "guess"})


def test_assembly_enlargement_scales_parts_with_one_origin():
    elements = [element(box(4, 4, 6, 6)), element(box(4.5, 4.5, 5.5, 5.5))]
    elements[0] = (*elements[0][:3], object())
    changed, reports = c.enlarge_landmarks(elements, [0, 0], box(0, 0, 20, 20), None, None)
    assert reports[0]["effective_footprint_scale"] == 1.5
    assert changed[0][0].covers(changed[1][0])
    assert changed[0][0].centroid.distance(elements[0][0].centroid) < 1e-9
    assert changed[1][0].area == pytest.approx(elements[1][0].area * 2.25)


def test_landmark_enlargement_respects_route():
    elements = [(*element(box(4, 4, 6, 6))[:3], object())]
    changed, reports = c.enlarge_landmarks(
        elements, [0], box(0, 0, 20, 20), None, box(6.05, 0, 10, 10)
    )
    assert reports[0]["effective_footprint_scale"] == 1
    assert changed[0][0].equals(elements[0][0])


def test_existing_configuration_is_not_mutated():
    from copy import deepcopy

    from memorymap_pipeline.config import DEFAULT_CONFIG

    original = deepcopy(DEFAULT_CONFIG)
    assert c.configure(original) == DEFAULT_CONFIG
    configured = c.configure({**original, "building_appearance_mode": "city_relief"})
    assert original == DEFAULT_CONFIG
    assert "service" not in configured["road_types"]
    assert configured["road_widths"]["residential"] == 0.4


def test_invalid_building_file_fails_instead_of_empty_layer(tmp_path):
    from memorymap_pipeline.buildings import download_and_build_buildings

    with pytest.raises(RuntimeError, match="could not be read"):
        download_and_build_buildings(
            bbox=None,
            center_lat=0,
            center_lon=0,
            transform={},
            map_width_mm=100,
            map_height_mm=100,
            margin_mm=0,
            buildings_file=str(tmp_path / "missing.geojson"),
        )


def test_generation_ordering_and_height_limits(tmp_path):

    from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
    from memorymap_pipeline.gpx_loader import Route, RoutePoint

    frame = MapFrame(34, -118, 1900, 1900, 20, 20)
    lat, lon = frame.print_to_lonlat(np.array([2, 18]), np.array([2, 2]))
    route = Route([RoutePoint(float(a), float(b)) for a, b in zip(lat, lon)])
    features = []
    for ident, p in [
        (2, box(7, 7, 7.3, 7.3)),
        (1, box(7.5, 7, 7.8, 7.3)),
        (3, box(11, 11, 13, 13)),
    ]:
        points = np.asarray(p.exterior.coords)
        lat, lon = frame.print_to_lonlat(points[:, 0], points[:, 1])
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "building": "yes",
                    "height": 6,
                    "_osm_id": ident,
                    "_osm_type": "way",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [list(zip(lon.tolist(), lat.tolist()))],
                },
            }
        )
    roads = tmp_path / "roads.geojson"
    lat, lon = frame.print_to_lonlat(np.array([0, 20]), np.array([19, 19]))
    roads.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"highway": "residential"},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": list(zip(lon.tolist(), lat.tolist())),
                        },
                    }
                ],
            }
        )
    )
    outputs = []
    for name, rows in [("first", features), ("second", list(reversed(features)))]:
        source = tmp_path / f"{name}.geojson"
        source.write_text(json.dumps({"type": "FeatureCollection", "features": rows}))
        result = generate_memory_map(
            GenerationRequest(
                route,
                frame,
                tmp_path / f"{name}.3mf",
                roads_file=roads,
                buildings_file=source,
                water_polygons=[],
                open_space_polygons=[],
                config={
                    "building_appearance_mode": "city_relief",
                    "building_height_multiplier": 2,
                    "building_height_max_mm": 3,
                },
            )
        )
        assert result.buildings_mesh.is_watertight
        assert result.buildings_mesh.is_winding_consistent
        assert result.buildings_mesh.bounds[1, 2] <= 3 + 1e-8
        assert result.stats["layer_states"]["buildings"] == "complete"
        outputs.append(result.buildings_mesh)
    assert np.array_equal(outputs[0].vertices, outputs[1].vertices)
    assert np.array_equal(outputs[0].faces, outputs[1].faces)


def test_robust_width_fallback_does_not_touch_protected_neighbor():
    ctx = c.context_for(context(), "balanced")
    elements = [
        element(box(4, 4, 4.2, 4.2)),
        element(box(4.35, 4, 4.55, 4.2)),
        element(box(5.1, 3.7, 6.5, 5.2), 30),
    ]
    allowed = box(0, 0, 20, 20)
    barrier = box(0, 4.65, 20, 5)
    result = optimize_buildings(elements, [2, 2, 6], ctx, allowed_region=allowed, barriers=barrier)
    result = c.rescue_groups(result, elements, [2, 2, 6], ctx, allowed, barrier, None, 0.8)
    for group in result.groups:
        assert group.geometry.intersection(elements[2][0]).area < 1e-8
        assert group.geometry.intersection(barrier).area < 1e-8
        assert allowed.covers(group.geometry)


def test_failed_buildings_preserve_previous_export(tmp_path, monkeypatch):
    from memorymap_pipeline import generation
    from memorymap_pipeline.gpx_loader import Route, RoutePoint

    target = tmp_path / "previous.3mf"
    target.write_bytes(b"previous successful model")

    def failure(**kwargs):
        raise RuntimeError("download incomplete")

    monkeypatch.setattr(generation, "download_and_build_buildings", failure)
    with pytest.raises(generation.LayerGenerationError) as error:
        generation.generate_memory_map(
            generation.GenerationRequest(
                Route([RoutePoint(34, -118), RoutePoint(34.001, -118.001)]),
                MapFrame(34, -118, 1000, 1000, 20, 20),
                target,
                include_roads=False,
            )
        )
    assert error.value.layer_states == {"buildings": "failed"}
    assert target.read_bytes() == b"previous successful model"
    assert not target.with_suffix(".audit.json").exists()


def test_city_mapping_precedes_manual_multiplier_mode():
    from memorymap_pipeline.config import DEFAULT_CONFIG

    cfg = c.configure(
        {
            **DEFAULT_CONFIG,
            "building_appearance_mode": "city_relief",
            "building_height_mode": "proportional",
            "building_height_multiplier": 2,
        }
    )
    assert cfg["building_height_mode"] == "architectural_relief"
    assert cfg["building_height_multiplier"] == 2


def test_city_empty_sources_are_complete_acquisition_with_empty_layers(tmp_path):
    from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
    from memorymap_pipeline.gpx_loader import Route, RoutePoint

    source = tmp_path / "empty.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    result = generate_memory_map(
        GenerationRequest(
            Route([RoutePoint(34, -118), RoutePoint(34.001, -118.001)]),
            MapFrame(34, -118, 1000, 1000, 20, 20),
            tmp_path / "empty.3mf",
            include_roads=False,
            roads_file=source,
            buildings_file=source,
            water_polygons=[],
            open_space_polygons=[],
            config={"building_appearance_mode": "city_relief"},
        )
    )
    assert result.stats["layer_states"]["buildings"] == "empty"
    assert result.stats["layer_states"]["roads"] == "disabled"
    assert result.buildings_mesh is None


def test_oblique_courtyard_contact_extrudes_without_filling_hole():
    from pathlib import Path
    from shapely.geometry import shape
    from memorymap_pipeline.mesh import route_mesh_from_polygon

    p = shape(
        json.loads((Path(__file__).parent / "fixtures/la_point_contact_courtyard.json").read_text())
    )
    mesh = route_mesh_from_polygon(p, 2)
    assert mesh.is_watertight
    assert mesh.is_winding_consistent
    assert mesh.volume == pytest.approx(p.area * 2, rel=1e-6)


def test_multipart_park_transform_preserves_parts_and_holes():
    from shapely.geometry import MultiPolygon, Polygon
    from memorymap_pipeline.buildings import _transform_shapely_polygon

    p = MultiPolygon(
        [
            Polygon(
                box(-118.001, 34, -118, 34.001).exterior,
                [box(-118.0008, 34.0002, -118.0006, 34.0004).exterior],
            ),
            box(-118.003, 34, -118.002, 34.001),
        ]
    )
    frame = MapFrame(34, -118, 1000, 1000, 20, 20)
    actual = _transform_shapely_polygon(p, 34, -118, {"map_frame": frame})
    expected = MultiPolygon(
        [_transform_shapely_polygon(part, 34, -118, {"map_frame": frame}) for part in p.geoms]
    )
    assert actual.equals_exact(expected, 1e-10)
    assert actual.is_valid


def test_city_preserves_substantial_narrow_silhouette():
    source = box(4, 4, 16, 4.5)
    result = optimize_buildings(
        [element(source)],
        [2],
        c.context_for(context(), "balanced"),
        allowed_region=box(0, 0, 20, 20),
        preserve_large_footprints=True,
    )
    assert not result.omitted
    assert result.decisions[0]["policy_class"] == "protected"
    assert result.footprints[0].equals(source)


def test_two_sources_map_to_one_bounded_representative():
    ctx = c.context_for(context(), "balanced")
    elements = [element(box(4, 4, 4.2, 4.2)), element(box(4.35, 4, 4.55, 4.2))]
    allowed = box(0, 0, 20, 20)
    result = optimize_buildings(
        elements, [2, 2.4], ctx, allowed_region=allowed, allow_grouping=False
    )
    result = c.typify_neighborhoods(result, elements, [2, 2.4], ctx, allowed, None, None, 0.8)
    assert not result.omitted
    assert len(result.groups) == 1
    assert result.groups[0].members == (0, 1)
    assert all(d["representative_source_indices"] == [0, 1] for d in result.decisions)
    assert allowed.covers(result.groups[0].geometry)


def test_crowded_sources_become_a_connected_stepped_mass():
    ctx = c.context_for(context(), "balanced")
    elems = [element(box(x, 4, x + 0.2, 4.2)) for x in [4, 4.35, 4.7, 5.05]]
    heights = [1.6, 1.6, 2.4, 2.4]
    result = optimize_buildings(
        elems, heights, ctx, allowed_region=box(0, 0, 20, 20), allow_grouping=False
    )
    result = c.typify_neighborhoods(result, elems, heights, ctx, box(0, 0, 20, 20), None, None, 0.8)
    assert not result.omitted
    assert len(result.groups) == 1
    assert result.groups[0].members == (0, 1, 2, 3)
    pieces = c.stepped_parts(
        result.groups[0].geometry, result.groups[0].members, elems, heights, 0.8
    )
    assert len(pieces) == 2
    assert sorted(h for p, h in pieces) == pytest.approx([1.6, 2.4])


def test_cached_area_mask_matches_reference_at_boundaries_and_tolerance():
    from memorymap_pipeline.spatial_area import AreaMask
    from shapely.ops import unary_union

    mask = unary_union([box(i, j, i + 0.6, j + 0.6) for i in range(5) for j in range(5)])
    local = AreaMask(mask, maximum_coordinates=8)
    candidates = [
        box(0.6, 0, 1, 0.6),
        box(0.6 - 1e-10, 0, 1, 0.6),
        box(0.5, 0.5, 1.1, 1.1),
        box(8, 8, 9, 9),
    ]
    for _ in range(3):
        for p in candidates:
            for threshold in [1e-10, 1e-8, 0.01]:
                assert local.exceeds(p, threshold) == (p.intersection(mask).area > threshold)


def test_local_obstacle_partition_preserves_union_area_and_threshold_decisions():
    from shapely.ops import unary_union

    from memorymap_pipeline.building_optimization import _LocalObstacles

    barriers = unary_union([box(i, j, i + 0.6, j + 0.6) for i in range(40) for j in range(40)])
    sources = [box(19.8, 19.8, 20.2, 20.2), box(100, 100, 101, 101)]
    overlapping = box(19, 19, 22, 22)
    obstacles = _LocalObstacles(sources, [barriers, overlapping])
    assert len(obstacles.fixed[0][1].pieces) > 1
    reference = unary_union([barriers, overlapping, sources[0]])
    for candidate in [
        box(19.9, 19.9, 20.7, 20.7),
        box(0.7, 0.7, 0.8, 0.8),
        *[box(0.1, 0.1, 0.1 + area / 0.1, 0.2) for area in (1e-8 - 1e-12, 1e-8, 1e-8 + 1e-12)],
    ]:
        actual = obstacles.intersection_area(candidate, 1)
        expected = candidate.intersection(reference).area
        assert actual == pytest.approx(expected, abs=1e-12)
        assert (actual <= 1e-8) == (expected <= 1e-8)


def test_landmark_enlargement_checks_previously_indexed_assemblies():
    sources = [element(box(10, 10, 11, 11), 40)]
    sources.extend(element(box(30 + i * 3, 10, 31 + i * 3, 11), 40) for i in range(68))
    sources.append(element(box(11.2, 10, 12.2, 11), 40))
    output, reports = c.enlarge_landmarks(sources, list(range(70)), box(0, 0, 300, 20), None, None)
    assert reports[0]["effective_footprint_scale"] == 1.25
    assert reports[69]["effective_footprint_scale"] == 1.1
    assert output[0][0].intersection(output[69][0]).area == 0


@pytest.mark.parametrize(
    "controls", [{}, {"building_height_multiplier": 2, "building_height_max_mm": 3}]
)
def test_city_assembly_preserves_source_height_ratios_through_caps_and_controls(tmp_path, controls):
    from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
    from memorymap_pipeline.gpx_loader import Route, RoutePoint

    frame = MapFrame(34, -118, 1900, 1900, 20, 20)
    lat, lon = frame.print_to_lonlat(np.array([2, 18]), np.array([2, 2]))
    route = Route([RoutePoint(float(a), float(b)) for a, b in zip(lat, lon)])
    features = []
    for ident, polygon, height, part in [
        (1, box(9, 9, 9.3, 10), 13, False),
        (2, box(9, 9.5, 9.3, 10), 24, True),
    ]:
        points = np.asarray(polygon.exterior.coords)
        lat, lon = frame.print_to_lonlat(points[:, 0], points[:, 1])
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "building:part" if part else "building": "yes",
                    "height": height,
                    "roof:shape": "flat",
                    "_osm_id": ident,
                    "_osm_type": "way",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [list(zip(lon.tolist(), lat.tolist()))],
                },
            }
        )
    source = tmp_path / "buildings.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    roads = tmp_path / "roads.geojson"
    roads.write_text('{"type":"FeatureCollection","features":[]}')
    output = tmp_path / "assembly.3mf"
    result = generate_memory_map(
        GenerationRequest(
            route,
            frame,
            output,
            roads_file=roads,
            buildings_file=source,
            water_polygons=[],
            open_space_polygons=[],
            config={"building_appearance_mode": "city_relief", **controls},
        )
    )
    assert result.buildings_mesh.is_watertight
    rows = json.loads(output.with_suffix(".audit.json").read_text())["buildings"]
    parent, part = [
        next(row["height_control"] for row in rows if row["source_id"] == str(i)) for i in (1, 2)
    ]
    assert parent["requested_top_mm"] < part["requested_top_mm"]
    assert parent["requested_top_mm"] / part["requested_top_mm"] == pytest.approx(13 / 24)
    assert parent["preset_aspect_limit_applied"] is (not controls)
    assert part["preset_aspect_limit_applied"] is (not controls)
    if controls:
        assert part["requested_top_mm"] == pytest.approx(3)


def test_verified_tall_accessory_type_uses_height_evidence_in_city_band():
    from dataclasses import replace

    dimensions = replace(element(box(0, 0, 1, 1), 60)[1], building_type="garage")
    assert c.height(dimensions) > 6
