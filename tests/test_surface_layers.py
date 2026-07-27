from __future__ import annotations

from shapely.geometry import box

from memorymap_pipeline.surface_layers import (
    build_conformal_surface_skin,
    landscape_surface_region,
)
from memorymap_pipeline.terrain import ElevationGrid, terrain_surface_from_grid


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
