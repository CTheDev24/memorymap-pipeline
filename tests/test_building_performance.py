"""Regression checks for exact local obstacle checks and route mesh validation."""

import numpy as np
from shapely import area, intersection, polygons
from shapely.geometry import Polygon, box
from shapely.ops import unary_union


def test_local_obstacles_match_global_union_without_double_counting():
    from memorymap_pipeline.building_optimization import _LocalObstacles

    sources = [box(0, 0, 3, 3), box(1, 1, 2, 2), box(1.5, 1, 2.5, 2)]
    barriers = [box(1, 1, 2, 2), box(1.8, 1, 2.8, 2), box(500, 500, 600, 600)]
    local = _LocalObstacles(sources, barriers)
    global_mask = unary_union([*barriers, *sources[1:]])
    for candidate in (
        box(1, 1, 2.2, 2.2),
        box(2.8, 1, 3, 2),
        Polygon(),
        box(1, 1, 1.00001, 1.00001),
    ):
        assert (
            abs(local.intersection_area(candidate, 0) - candidate.intersection(global_mask).area)
            < 1e-12
        )


def test_route_projection_matches_all_triangles_including_overhang_and_degenerate_faces():
    from memorymap_pipeline.generation import _building_route_overlap

    route = box(0, 0, 1, 1).difference(box(0.4, 0.4, 0.6, 0.6))
    triangles = np.array(
        [
            [[-2, 0.5], [2, 0.5], [2, 0.8]],  # overhanging roof crossing route
            [[0, 0], [0, 1], [0, 1]],  # vertical side projects to a line
            [[0.45, 0.45], [0.55, 0.45], [0.55, 0.55]],  # inside corridor hole
            [[10, 10], [11, 10], [11, 11]],  # far away
            [[1, 0], [2, 0], [1, 1]],  # boundary-only contact
        ]
    )
    projected = polygons(triangles)
    projected = projected[area(projected) > 1e-12]
    expected = float(np.sum(area(intersection(projected, route))))
    updates = []
    actual = _building_route_overlap(
        triangles, route, lambda fraction: updates.append(fraction), chunk_size=2
    )
    assert abs(actual - expected) < 1e-12 and actual > 0
    assert updates == sorted(updates) and updates[-1] == 1
    assert _building_route_overlap(triangles, Polygon()) == 0


def test_width_cache_is_exact_and_scoped_to_geometry():
    from memorymap_pipeline.building_generalization import local_core_width_mm

    cache = {}
    original = box(0, 0, 3, 2)
    narrowed = box(0, 0, 3, 0.5)
    assert local_core_width_mm(original, cache=cache) == local_core_width_mm(original)
    assert local_core_width_mm(narrowed, cache=cache) == local_core_width_mm(narrowed)
    assert local_core_width_mm(original, cache=cache) > local_core_width_mm(narrowed, cache=cache)
    assert len(cache) == 2


def test_optimizer_does_not_reunion_global_barriers_for_each_building(monkeypatch):
    import memorymap_pipeline.building_optimization as optimization
    from memorymap_pipeline.buildings import BuildingDimensions
    from memorymap_pipeline.map_frame import MapFrame
    from memorymap_pipeline.print_scale import PrintScaleContext

    barriers = unary_union([box(x, -1, x + 0.1, 30) for x in range(20)])
    elements = [
        (
            box(x + 0.2, y, x + 0.8, y + 0.6),
            BuildingDimensions(0, 6, 0, "flat", "along"),
            False,
            None,
        )
        for x in range(10)
        for y in range(10)
    ]
    real_union = optimization.unary_union
    global_unions = []

    def tracked_union(geometries):
        geometries = list(geometries)
        if any(g is barriers for g in geometries):
            global_unions.append(len(geometries))
        return real_union(geometries)

    monkeypatch.setattr(optimization, "unary_union", tracked_union)
    context = PrintScaleContext.from_frame(MapFrame(0, 0, 30, 30, 30, 30), {"line_width_mm": 0.42})
    updates = []
    result = optimization.optimize_buildings(
        elements,
        [1.0] * len(elements),
        context,
        allowed_region=box(-1, -1, 30, 30),
        barriers=barriers,
        progress_callback=lambda f, message: updates.append(f),
    )
    assert len(result.decisions) == len(elements)
    assert len(global_unions) <= 1
    assert updates == sorted(updates) and updates[-1] == 1.0


def test_generation_rejects_mesh_overhang_into_route_before_export(tmp_path, monkeypatch):
    from pathlib import Path

    import pytest
    from trimesh.creation import box as mesh_box

    from memorymap_pipeline import generation
    from memorymap_pipeline.gpx_loader import load_route_from_gpx
    from memorymap_pipeline.map_frame import MapFrame

    fixtures = Path(__file__).parent / "fixtures"
    route = load_route_from_gpx(fixtures / "frame_route.gpx")
    frame = MapFrame.fit_route(route.points, 120, 90, 5)

    def crossing_mesh(**kwargs):
        center = kwargs["route_exclusion"].representative_point()
        mesh = mesh_box(extents=(1, 1, 1))
        mesh.apply_translation((center.x, center.y, 2))
        return box(center.x - 0.5, center.y - 0.5, center.x + 0.5, center.y + 0.5), mesh

    monkeypatch.setattr(generation, "download_and_build_buildings", crossing_mesh)
    output = tmp_path / "blocked.3mf"
    with pytest.raises(ValueError, match="crosses the route clearance"):
        generation.generate_memory_map(
            generation.GenerationRequest(
                route=route,
                frame=frame,
                output_path=output,
                include_roads=False,
                config={"building_generalization_mode": "print_optimized"},
            )
        )
    assert not output.exists()
    assert not output.with_suffix(".audit.json").exists()

def test_batched_widths_match_scalar_diagnostics_and_failure_counts(monkeypatch):
    import memorymap_pipeline.building_generalization as generalization
    from memorymap_pipeline.map_frame import MapFrame
    from memorymap_pipeline.print_scale import PrintScaleContext

    rng = np.random.default_rng(42)
    shapes = [box(0, 0, float(w), float(h)) for w, h in rng.uniform(.001, 5, (260, 2))]
    shapes += [Polygon(), Polygon([(0, 0), (1, 1), (1, 0), (0, 1)]),
               box(0, 0, 8, 8).difference(box(.2, .2, 7.8, 7.8))]
    shapes += [shapes[0], shapes[-1]]
    ctx = PrintScaleContext.from_frame(MapFrame(0, 0, 100, 100, 100, 100), {})
    batch = generalization._cache_width_batch
    monkeypatch.setattr(generalization, '_cache_width_batch', lambda *args: None)
    expected = generalization.measure_footprints(shapes, ctx)
    monkeypatch.setattr(generalization, '_cache_width_batch', batch)
    cache = {}
    actual = generalization.measure_footprints(shapes, ctx, width_cache=cache)
    assert actual == expected
    assert actual['width_measurement_failed_count'] == 2
    for polygon in shapes:
        if polygon.is_valid and not polygon.is_empty:
            assert cache[polygon.wkb] == generalization.local_core_width_mm(polygon)
    assert generalization.measure_footprints(shapes, ctx, width_cache=cache) == expected


def test_fast_row_tags_preserve_mixed_osm_values_and_index_identity():
    import pandas as pd

    from memorymap_pipeline.buildings import _tags_from_gdf_row

    row = pd.Series({
        'building': 'yes', 'height': ['4', '6'], 'levels': np.array([1, 2]),
        'number': np.int64(2), 'flag': np.bool_(True),
        'missing': None, 'nan': float('nan'), 'geometry': box(0, 0, 1, 1),
    }, name=('way', 12345))
    tags = _tags_from_gdf_row(row, list(row.index))
    for key in ('building', 'height', 'levels', 'number', 'flag'):
        assert tags[key] is row[key]
    assert tags['_osm_type'] == 'way' and tags['_osm_id'] == 12345
    assert not {'missing', 'nan', 'geometry'} & tags.keys()
