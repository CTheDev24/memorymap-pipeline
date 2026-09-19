import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box, shape
from shapely.ops import unary_union

from memorymap_pipeline.building_grouping import group_footprints
from memorymap_pipeline.buildings import download_and_build_buildings
from memorymap_pipeline.map_frame import MapFrame


def grid():
    return [
        box(x * 0.4, y * 0.4, x * 0.4 + 0.25, y * 0.4 + 0.25) for x in range(3) for y in range(3)
    ]


def test_groups_reach_width_without_losing_sources():
    footprints = grid()
    groups = group_footprints(footprints, range(len(footprints)))
    assert groups
    members = [i for group in groups for i in group.members]
    assert len(members) == len(set(members))
    for group in groups:
        assert not group.geometry.buffer(-0.4, join_style=2).is_empty
        assert all(footprints[i].difference(group.geometry).area < 1e-12 for i in group.members)
        assert max(np.diff(np.array(group.geometry.bounds).reshape(2, 2), axis=0)[0]) <= 4


@pytest.mark.parametrize("kind", ["road", "route", "water", "protected_building"])
def test_groups_do_not_cross_barriers(kind):
    footprints = grid()
    barrier = box(0.28, -0.1, 0.38, 1.5)
    eligible = list(range(len(footprints)))
    if kind == "protected_building":
        footprints.append(barrier)
        barriers = None
    else:
        barriers = barrier
    groups = group_footprints(footprints, eligible, barriers)
    assert all(g.geometry.intersection(barrier).area < 1e-10 for g in groups)


def test_unresolvable_narrow_row_and_isolated_source_are_preserved():
    footprints = [box(i * 0.4, 0, i * 0.4 + 0.25, 0.25) for i in range(6)]
    barriers = unary_union([box(-1, -0.5, 4, -0.001), box(-1, 0.251, 4, 0.8)])
    assert group_footprints(footprints, range(6), barriers) == []
    assert group_footprints([box(0, 0, 0.2, 0.2)], [0]) == []


def test_tiny_source_can_join_a_substantial_anchor():
    groups = group_footprints([box(0, 0, 1, 1), box(1.1, 0.2, 1.3, 0.4)], [0, 1])
    assert len(groups) == 1
    assert groups[0].members == (0, 1)


def test_group_geometry_is_deterministic_under_source_reordering():
    footprints = grid()
    first = group_footprints(footprints, range(9))
    second = group_footprints(list(reversed(footprints)), range(9))
    assert unary_union([g.geometry for g in first]).equals(
        unary_union([g.geometry for g in second])
    )


def test_grouping_integrates_extrusion_and_source_mapping(tmp_path):
    frame = MapFrame(
        center_lat=41.88,
        center_lon=-87.63,
        coverage_width_m=200,
        coverage_height_m=200,
        print_width_mm=20,
        print_height_mm=20,
    )
    features = []
    for i, polygon in enumerate(grid()):
        xy = np.array(polygon.exterior.coords) + 8
        lat, lon = frame.print_to_lonlat(xy[:, 0], xy[:, 1])
        features.append(
            {
                "type": "Feature",
                "properties": {"id": str(i), "building": "house", "height": 4},
                "geometry": {"type": "Polygon", "coordinates": [list(zip(lon, lat))]},
            }
        )
    file = tmp_path / "buildings.geojson"
    file.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    records = []
    _, mesh = download_and_build_buildings(
        bbox=None,
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        map_width_mm=20,
        map_height_mm=20,
        margin_mm=0,
        buildings_file=str(file),
        diagnostics=records,
        embed_depth_mm=0.2,
        grouping={"width_mm": 0.8, "gap_mm": 0.4, "span_mm": 4, "max_height_mm": 3},
    )
    assert mesh.is_watertight
    assert len(records) == 9
    grouped = [r for r in records if r["status"] == "grouped"]
    assert len(grouped) >= 2
    assert mesh.metadata["building_grouping"]["grouped_sources"] == len(grouped)
    for r in grouped:
        assert r["output_face_ranges"]
        assert r["grouped_height_mm"] <= 3
        assert not shape(r["output_print_geometry"]).buffer(-0.4, join_style=2).is_empty
    assert len({tuple(map(tuple, r["output_face_ranges"])) for r in grouped}) < len(grouped)


def test_water_failure_cannot_be_treated_as_empty_barrier(monkeypatch):
    import geopandas as gpd

    from memorymap_pipeline.water import download_water_polygons

    monkeypatch.setattr(
        gpd, "read_file", lambda *a, **kw: (_ for _ in ()).throw(OSError("offline"))
    )
    with pytest.raises(ValueError, match="barriers could not be verified"):
        download_water_polygons(
            (0, 1, 0, 1), 0.5, 0.5, {}, 20, 20, water_file=Path("absent"), strict=True
        )


def test_grouping_respects_plate_boundary_and_does_not_overlap_groups():
    footprints = grid() + [p.buffer(0) for p in [box(1.2, 0, 1.4, 0.2), box(1.6, 0, 1.8, 0.2)]]
    allowed = box(0, 0, 2, 2)
    groups = group_footprints(footprints, range(len(footprints)), allowed_region=allowed)
    assert all(allowed.covers(group.geometry) for group in groups)
    for i, group in enumerate(groups):
        assert all(
            group.geometry.intersection(other.geometry).area < 1e-10 for other in groups[i + 1 :]
        )


def test_grouping_generation_omits_alleys_only_when_enabled(tmp_path, monkeypatch):
    from memorymap_pipeline import generation
    from memorymap_pipeline.gpx_loader import load_route_from_gpx

    fixtures = Path(__file__).parent / "fixtures"
    route = load_route_from_gpx(fixtures / "frame_route.gpx")
    frame = MapFrame.fit_route(route.points, 190, 240, route_padding_mm=5.6)
    original = generation.download_and_build_roads
    calls = []

    def capture(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(generation, "download_and_build_roads", capture)
    for enabled in (False, True):
        generation.generate_memory_map(
            generation.GenerationRequest(
                route=route,
                frame=frame,
                output_path=tmp_path / f"{enabled}.3mf",
                roads_file=fixtures / "frame_roads.geojson",
                buildings_file=fixtures / "frame_buildings.geojson",
                water_polygons=[],
                export_model=False,
                config={"building_grouping_enabled": enabled},
            )
        )
    assert "alley" not in calls[0]["excluded_service_types"]
    assert {"alley", "driveway"}.issubset(calls[1]["excluded_service_types"])
    assert calls[0]["road_widths"]["residential"] == 1.1
    assert calls[1]["road_widths"]["residential"] == 0.4
    assert calls[0]["road_widths"]["primary"] == calls[1]["road_widths"]["primary"]


def test_grouped_output_passes_local_width_screen():
    from memorymap_pipeline.footprint_benchmark import screen_footprint

    footprints = [box(0, 0, 0.25, 0.25), box(0.35, 0.05, 0.60, 0.30), box(0.7, 0.15, 0.95, 0.40)]
    groups = group_footprints(footprints, range(len(footprints)))
    assert groups
    assert all(screen_footprint(group.geometry)["status"] == "not_flagged" for group in groups)
