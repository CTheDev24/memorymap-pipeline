from pathlib import Path
from dataclasses import replace

import numpy as np
import pytest

from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.map_styles import resolve_request
from memorymap_pipeline.desktop.project import DesktopProject
from memorymap_pipeline.terrain import ElevationGrid

FIXTURES = Path(__file__).parent / "fixtures"


def request(tmp_path, style=None):
    return GenerationRequest(
        route=load_route_from_gpx(FIXTURES / "frame_route.gpx"),
        frame=MapFrame(29.76, -95.37, 180, 140, 120, 90, 5),
        output_path=tmp_path / f"{style or 'legacy'}.3mf", map_style=style,
        roads_file=FIXTURES / "frame_roads.geojson",
        buildings_file=FIXTURES / "frame_buildings.geojson",
    )


@pytest.mark.parametrize("style,profile,buildings,terrain", [
    ("cityscape", "urban", True, False), ("street_grid", "urban", False, False),
    ("terrain", "landscape", False, True),
])
def test_styles_resolve_to_established_engines(tmp_path, style, profile, buildings, terrain):
    resolved = resolve_request(request(tmp_path, style))
    assert resolved.config["style_profile"] == profile
    assert resolved.include_buildings == buildings
    assert resolved.config["terrain_enabled"] == terrain


def test_legacy_projects_migrate_without_changing_geometry_settings(tmp_path):
    original = DesktopProject(frame=request(tmp_path).frame, include_buildings=True,
                              include_terrain=True, route_layer_height_mm=.24,
                              flat_border_enabled=True)
    value = original.to_dict()
    value["version"] = 4
    value["map_style"] = "street_grid"  # new fields are ignored by older schema migration
    migrated = DesktopProject.from_dict(value)
    assert migrated.map_style is None
    assert migrated.include_buildings and migrated.include_terrain
    assert migrated.flat_border_enabled and migrated.route_layer_height_mm == .24
    legacy = request(tmp_path)
    assert resolve_request(legacy) is legacy
    assert DesktopProject.from_json(migrated.to_json()) == migrated


@pytest.mark.parametrize("style", ["cityscape", "street_grid", "terrain"])
def test_representative_style_generations(tmp_path, style):
    req = request(tmp_path, style)
    if style == "terrain":
        grid = ElevationGrid(np.array([[10., 30., 80.], [0., 35., 60.], [5., 20., 40.]]),
                             29.755, 29.765, -95.375, -95.365, "fixture")
        req = replace(req, elevation_grid=grid, include_roads=False,
                      config={"ground_cover_mode": "off", "exposed_land_enabled": False})
    result = generate_memory_map(req)
    assert result.output_path.is_file()
    assert result.route_mesh is not None
    assert (result.buildings_mesh is not None) == (style == "cityscape")
    assert (result.stats["terrain"] is not None) == (style == "terrain")
    if style == "terrain":
        assert result.stats["terrain"]["relief_mm"] > 0
    for mesh in result.meshes.values():
        assert mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0
        assert np.isfinite(mesh.vertices).all()
        assert mesh.bounds[0, 0] >= -1e-6 and mesh.bounds[1, 0] <= 120 + 1e-6
        assert mesh.bounds[0, 1] >= -1e-6 and mesh.bounds[1, 1] <= 90 + 1e-6
