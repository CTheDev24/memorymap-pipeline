from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import LineString

from memorymap_pipeline.config import DEFAULT_CONFIG, load_config
from memorymap_pipeline.generation import _merged_config
from memorymap_pipeline.mesh import refine_mesh_edges, route_mesh_from_polygon
from memorymap_pipeline.terrain import (
    ElevationGrid,
    build_terrain_mesh,
    drape_road_mesh,
    drape_route_mesh,
    terrain_surface_from_grid,
)
from memorymap_pipeline.terrain_presets import TERRAIN_PRESETS, preset_settings
from memorymap_pipeline.water import (
    build_terrain_mesh_with_water,
    build_vector_water_mesh,
    prepare_water_bodies,
)


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "terrain_profiles.json").read_text()
)


def _surface(name: str):
    fixture = FIXTURES[name]
    values = np.asarray(fixture["elevations_m"], dtype=float)
    settings = preset_settings(name)
    grid = ElevationGrid(values, 0.0, 1.0, 0.0, 1.0, f"fixture-{name}")
    return terrain_surface_from_grid(
        grid,
        120.0,
        90.0,
        fixture["horizontal_span_m"],
        settings["terrain_max_relief_mm"],
        settings["terrain_min_relief_mm"],
    )


def test_presets_are_safe_and_explicit_user_values_win() -> None:
    assert set(TERRAIN_PRESETS) == {"flat-urban", "rolling-terrain", "mountain-coast"}
    for name in TERRAIN_PRESETS:
        config = _merged_config({"terrain_preset": name})
        assert 0 < config["terrain_min_relief_mm"] <= config["terrain_max_relief_mm"] <= 4
        assert config["water_recess_mm"] == pytest.approx(0.4)
    overridden = _merged_config(
        {"terrain_preset": "mountain-coast", "terrain_max_relief_mm": 2.6,
         "terrain_grid_size": 72}
    )
    assert overridden["terrain_max_relief_mm"] == pytest.approx(2.6)
    assert overridden["terrain_grid_size"] == 72
    assert DEFAULT_CONFIG["terrain_preset"] is None


def test_json_config_applies_preset_before_explicit_overrides(tmp_path: Path) -> None:
    path = tmp_path / "terrain.json"
    path.write_text(json.dumps({
        "terrain_preset": "mountain-coast",
        "terrain_max_relief_mm": 2.8,
    }))
    config = load_config(path)
    assert config["terrain_max_relief_mm"] == pytest.approx(2.8)
    assert config["terrain_min_relief_mm"] == pytest.approx(2.0)
    assert config["terrain_grid_size"] == 128


@pytest.mark.parametrize("name", list(TERRAIN_PRESETS))
def test_offline_profiles_have_bounded_supported_relief(name: str) -> None:
    surface = _surface(name)
    settings = preset_settings(name)
    assert surface.heights_mm.min() >= 0
    assert surface.heights_mm.max() <= settings["terrain_max_relief_mm"] + 1e-9
    assert surface.analysis.target_relief_mm >= settings["terrain_min_relief_mm"] - 1e-9
    terrain = build_terrain_mesh(surface, base_thickness_mm=1.6)
    assert terrain.is_watertight and terrain.is_winding_consistent
    assert terrain.bounds[0, 2] == pytest.approx(-1.6)
    assert terrain.volume > 0


@pytest.mark.parametrize("name", list(TERRAIN_PRESETS))
def test_route_stays_continuous_visible_and_constant_width(name: str) -> None:
    surface = _surface(name)
    centerline = LineString([(8.0, 20.0), (45.0, 48.0), (112.0, 70.0)])
    width = 1.2
    region = centerline.buffer(width / 2, cap_style=2, join_style=1)
    mesh = refine_mesh_edges(route_mesh_from_polygon(region, 2.2, -0.2), 2.4)
    result = drape_route_mesh(
        mesh, centerline, surface, route_width_mm=width, visible_height_mm=2.0,
        smoothing_distance_mm=preset_settings(name)["route_terrain_smoothing_distance_mm"],
    )
    assert result.is_watertight and len(result.split(only_watertight=False)) == 1
    original_top = np.isclose(mesh.vertices[:, 2], 2.0)
    terrain_at_top = surface.sample(mesh.vertices[original_top, 0], mesh.vertices[original_top, 1])
    # Curved join vertices can project a fraction of a nozzle outside the ideal normal;
    # retain effectively all of the requested 2 mm visible route height.
    assert np.min(result.vertices[original_top, 2] - terrain_at_top) >= 1.98


@pytest.mark.parametrize("name", list(TERRAIN_PRESETS))
def test_highway_directional_smoothing_remains_supported(name: str) -> None:
    surface = _surface(name)
    centerline = LineString([(8.0, 45.0), (112.0, 45.0)])
    region = centerline.buffer(1.2, cap_style=2, join_style=1)
    mesh = refine_mesh_edges(route_mesh_from_polygon(region, 1.0, -0.2), 3.0)
    corridor = SimpleNamespace(centerline=centerline, region=region, width_mm=2.4,
                               classification="motorway")
    config = _merged_config({"terrain_preset": name})
    result = drape_road_mesh(
        mesh, surface, (corridor,), visible_height_mm=0.8,
        minimum_visible_height_mm=0.4,
        smoothing_distances_mm=config["road_terrain_smoothing_distances_mm"],
    )
    assert result.is_watertight
    bottom = np.isclose(mesh.vertices[:, 2], -0.2)
    assert result.vertices[bottom, 2] == pytest.approx(
        mesh.vertices[bottom, 2] + surface.sample(mesh.vertices[bottom, 0], mesh.vertices[bottom, 1])
    )


def test_flat_river_fixture_builds_recessed_water_with_structural_support() -> None:
    surface = _surface("flat-urban")
    river = LineString([(5, 30), (55, 42), (115, 35)]).buffer(4, resolution=8)
    bodies = prepare_water_bodies([river], surface, recess_mm=0.4, margin_mm=5.0)
    water = build_vector_water_mesh(bodies, thickness_mm=0.6)
    terrain = build_terrain_mesh_with_water(surface, 1.6, bodies, 0.6, 0.4)
    assert water is not None and water.is_watertight
    assert terrain.is_watertight and terrain.volume > 0
    assert water.bounds[1, 2] <= float(surface.heights_mm.max()) - 0.4 + 1e-7
    assert water.bounds[1, 2] - water.bounds[0, 2] == pytest.approx(0.6)
