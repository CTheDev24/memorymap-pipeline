from __future__ import annotations

from types import SimpleNamespace

import pytest
from shapely.geometry import LineString, Point, box

import memorymap_pipeline.surface_layers as surface_layers
from memorymap_pipeline.surface_layers import (
    COASTAL_EXPOSURE_EVIDENCE_TAGS,
    EXPOSED_LAND_TAGS,
    build_conformal_surface_skin,
    download_coastal_exposure_evidence,
    download_exposed_land_polygons,
    landscape_surface_region,
    recess_terrain_surface,
)
from memorymap_pipeline.terrain import (
    ElevationGrid,
    build_terrain_mesh,
    terrain_surface_from_grid,
)


def _surface():
    return terrain_surface_from_grid(
        ElevationGrid(
            elevations_m=[
                [20.0, 22.0, 24.0, 26.0],
                [18.0, 20.0, 22.0, 24.0],
                [16.0, 18.0, 20.0, 22.0],
                [14.0, 16.0, 18.0, 20.0],
            ],
            south=0.0,
            north=1.0,
            west=0.0,
            east=1.0,
            source="fixture",
        ),
        width_mm=90.0,
        height_mm=60.0,
        horizontal_span_m=1_000.0,
    )


class _Features:
    def __init__(self, geometries):
        self._features = [SimpleNamespace(geometry=geometry) for geometry in geometries]

    def iterrows(self):
        return enumerate(self._features)


def _print_transform():
    return {
        "scale": 0.001,
        "min_x": 0.0,
        "min_y": 0.0,
        "offset_x": 50.0,
        "offset_y": 50.0,
    }


def test_exposed_land_query_includes_expanded_polygon_tags() -> None:
    assert "blockfield" in EXPOSED_LAND_TAGS["natural"]
    assert {"pebblestone", "stone", "bare_ground", "ground"} <= set(
        EXPOSED_LAND_TAGS["surface"]
    )


def test_exposed_land_rejects_non_polygon_surface_features(monkeypatch) -> None:
    monkeypatch.setattr(
        surface_layers,
        "_load_osm_features",
        lambda *args, **kwargs: _Features(
            [LineString([(0.0, 0.0), (0.001, 0.0)]), box(0.0, 0.0, 0.001, 0.001)]
        ),
    )

    polygons = download_exposed_land_polygons(
        (-1.0, 1.0, -1.0, 1.0),
        0.0,
        0.0,
        _print_transform(),
        100.0,
        100.0,
    )

    assert len(polygons) == 1
    assert polygons[0].geom_type == "Polygon"


def test_coastal_evidence_query_and_buffer_are_separate_from_exposed_polygons(
    monkeypatch,
) -> None:
    captured = {}

    def load(*args, **kwargs):
        captured["tags"] = args[3]
        return _Features(
            [
                Point(0.0, 0.0),
                LineString([(0.0, 0.0), (0.001, 0.0)]),
                box(0.0, 0.0, 0.001, 0.001),
            ]
        )

    monkeypatch.setattr(surface_layers, "_load_osm_features", load)
    evidence = download_coastal_exposure_evidence(
        (-1.0, 1.0, -1.0, 1.0),
        0.0,
        0.0,
        _print_transform(),
        100.0,
        100.0,
        buffer_mm=2.0,
    )

    assert captured["tags"] == COASTAL_EXPOSURE_EVIDENCE_TAGS
    assert len(evidence) == 2
    assert all(item.geom_type == "Polygon" for item in evidence)
    assert evidence[0].area == pytest.approx(Point(50.0, 50.0).buffer(2.0).area)


def test_coastal_evidence_requires_positive_print_buffer() -> None:
    with pytest.raises(ValueError, match="buffer"):
        download_coastal_exposure_evidence(
            (-1.0, 1.0, -1.0, 1.0),
            0.0,
            0.0,
            _print_transform(),
            100.0,
            100.0,
            buffer_mm=0.0,
        )


def test_landscape_region_leaves_water_and_exposed_ground_uncovered() -> None:
    water = box(20.0, 5.0, 35.0, 55.0)
    beach = box(35.0, 5.0, 45.0, 55.0)

    region = landscape_surface_region(
        90.0,
        60.0,
        5.0,
        water_geometries=[water],
        exposed_geometries=[beach],
    )

    assert region.contains(box(50.0, 10.0, 80.0, 50.0))
    assert not region.intersects(water.buffer(-0.1))
    assert not region.intersects(beach.buffer(-0.1))
    assert region.bounds == (5.0, 5.0, 85.0, 55.0)


def test_conformal_surface_skin_is_supported_and_watertight() -> None:
    surface = _surface()
    region = box(0.0, 0.0, 55.0, 60.0)

    skin = build_conformal_surface_skin(
        surface,
        region,
        visible_thickness_mm=0.4,
        embed_depth_mm=0.2,
    )

    assert skin is not None
    assert skin.is_watertight
    assert skin.is_winding_consistent
    assert skin.volume > 0.0
    assert skin.bounds[1, 2] <= surface.heights_mm.max() + 0.4 + 1e-6
    assert skin.bounds[0, 2] >= surface.heights_mm.min() - 0.2 - 1e-6
    assert skin.bounds[0, 0] >= 0.0
    assert skin.bounds[1, 0] <= 60.0


def test_conformal_surface_skin_can_be_recessed_below_adjacent_skin() -> None:
    surface = _surface()
    water = build_conformal_surface_skin(
        surface,
        box(20.0, 10.0, 40.0, 50.0),
        visible_thickness_mm=0.4,
        embed_depth_mm=0.2,
        surface_offset_mm=-0.4,
    )

    assert water is not None
    support = surface.sample(water.vertices[:, 0], water.vertices[:, 1])
    offsets = water.vertices[:, 2] - support
    assert offsets.max() == pytest.approx(0.0)
    assert offsets.min() == pytest.approx(-0.6)


def test_raster_aligned_water_recess_keeps_terrain_watertight() -> None:
    surface = _surface()
    waterway = box(0.0, 0.0, 60.0, 60.0)
    recessed = recess_terrain_surface(surface, waterway, 0.4)
    base = build_terrain_mesh(recessed, 1.6)

    assert base.is_watertight
    assert base.is_winding_consistent
    assert (recessed.heights_mm < surface.heights_mm).any()
    assert recessed.heights_mm.max() <= surface.heights_mm.max()


def test_conformal_skin_respects_an_exact_rectangular_trim() -> None:
    surface = _surface()
    trim = box(5.0, 5.0, 85.0, 55.0)

    skin = build_conformal_surface_skin(
        surface,
        box(0.0, 0.0, 90.0, 60.0),
        clip_region=trim,
    )

    assert skin is not None
    assert skin.is_watertight
    assert skin.bounds[0, :2] == pytest.approx((5.0, 5.0))
    assert skin.bounds[1, :2] == pytest.approx((85.0, 55.0))


def test_conformal_skin_keeps_corner_touching_islands_watertight() -> None:
    surface = _surface()
    # These small regions select two DEM triangles that meet at exactly one
    # grid corner but do not share an edge, matching diagonal coastal and
    # waterway rasterization seen in Big Sur.
    region = box(19.0, 45.5, 21.0, 47.5).union(
        box(39.0, 32.2, 41.0, 34.4)
    )

    skin = build_conformal_surface_skin(surface, region)

    assert skin is not None
    assert skin.is_watertight
    assert skin.is_winding_consistent
