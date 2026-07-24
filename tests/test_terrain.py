import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import requests
from PIL import Image
from shapely.geometry import LineString, Point, box
from trimesh import Trimesh

from memorymap_pipeline import generation
from memorymap_pipeline.config import load_config
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.mesh import export_3mf, refine_mesh_edges, route_mesh_from_polygon
from memorymap_pipeline.terrain import (
    ElevationGrid,
    analyze_terrain,
    build_terrain_mesh,
    drape_mesh,
    drape_road_mesh,
    drape_route_mesh,
    terrain_surface_from_grid,
)
from memorymap_pipeline.terrain_providers import (
    TerrariumProvider,
    Usgs3depProvider,
    _decode_terrarium,
)
from memorymap_pipeline.water import (
    WATER_TAGS,
    build_terrain_mesh_with_water,
    build_vector_water_mesh,
    download_water_polygons,
    prepare_water_bodies,
)


def _grid(values: list[list[float]]) -> ElevationGrid:
    return ElevationGrid(
        np.asarray(values, dtype=float),
        south=29.7,
        north=29.8,
        west=-95.4,
        east=-95.3,
        source="fixture",
    )


def test_flat_houston_like_frame_uses_full_three_mm_relief() -> None:
    analysis = analyze_terrain(np.array([[10.0, 10.5], [11.0, 11.5]]), 10_000.0)
    assert analysis.flatness_rating == 5
    assert analysis.target_relief_mm == pytest.approx(3.0, abs=0.01)
    assert analysis.vertical_scale_mm_per_m > 1.0


def test_rugged_frame_reduces_print_relief() -> None:
    analysis = analyze_terrain(np.array([[0.0, 400.0], [800.0, 1200.0]]), 2_000.0)
    assert analysis.flatness_rating == 0
    assert analysis.target_relief_mm == pytest.approx(1.5)


def test_surface_normalizes_robust_elevations_and_samples_print_space() -> None:
    surface = terrain_surface_from_grid(
        _grid([[30.0, 40.0], [10.0, 20.0]]),
        width_mm=100.0,
        height_mm=80.0,
        horizontal_span_m=10_000.0,
    )
    assert surface.heights_mm.min() == pytest.approx(0.0)
    assert surface.heights_mm.max() == pytest.approx(surface.analysis.target_relief_mm)
    assert surface.sample(0.0, 80.0) == pytest.approx(surface.heights_mm[0, 0])
    assert surface.sample(100.0, 0.0) == pytest.approx(surface.heights_mm[-1, -1])


def test_terrain_mesh_is_watertight_with_structural_bottom() -> None:
    surface = terrain_surface_from_grid(
        _grid([[30.0, 40.0, 45.0], [20.0, 25.0, 35.0], [10.0, 15.0, 20.0]]),
        width_mm=100.0,
        height_mm=80.0,
        horizontal_span_m=10_000.0,
    )
    mesh = build_terrain_mesh(surface, base_thickness_mm=1.0)
    assert mesh.is_watertight
    assert mesh.is_winding_consistent
    assert mesh.volume > 0.0
    assert mesh.bounds[0, 2] == pytest.approx(-1.0)
    assert mesh.bounds[1, 2] == pytest.approx(surface.analysis.target_relief_mm)


def test_drape_preserves_visible_feature_height_over_local_surface() -> None:
    surface = terrain_surface_from_grid(
        _grid([[30.0, 40.0], [10.0, 20.0]]),
        width_mm=100.0,
        height_mm=80.0,
        horizontal_span_m=10_000.0,
    )
    feature = route_mesh_from_polygon(box(10.0, 10.0, 20.0, 20.0), 0.8, -0.2)
    draped = drape_mesh(feature, surface)
    expected_offsets = surface.sample(feature.vertices[:, 0], feature.vertices[:, 1])
    assert draped.vertices[:, 2] == pytest.approx(feature.vertices[:, 2] + expected_offsets)


def test_major_road_smoothing_changes_only_supported_top_vertices() -> None:
    surface = terrain_surface_from_grid(
        _grid(
            [
                [0, 0, 0],
                [0, 100, 0],
                [0, 0, 0],
            ]
        ),
        width_mm=100.0,
        height_mm=100.0,
        horizontal_span_m=1_000.0,
    )
    feature = Trimesh(
        vertices=np.array(
            [
                [50.0, 50.0, -0.2],
                [50.0, 50.0, 0.8],
                [10.0, 10.0, 0.8],
            ]
        ),
        faces=np.empty((0, 3), dtype=int),
        process=False,
    )
    raw = drape_mesh(feature, surface)
    smoothed = drape_mesh(
        feature,
        surface,
        smooth_top_region=box(40.0, 40.0, 60.0, 60.0),
        smoothing_radius_mm=50.0,
        minimum_visible_height_mm=0.4,
    )

    # The underside remains on the raw terrain so the road cannot float.
    assert smoothed.vertices[0, 2] == pytest.approx(raw.vertices[0, 2])
    # The peak is softened while retaining at least 0.4 mm above local terrain.
    assert smoothed.vertices[1, 2] < raw.vertices[1, 2]
    assert smoothed.vertices[1, 2] >= surface.sample(50.0, 50.0) + 0.4
    # Vertices outside the selected major-road footprint are unchanged.
    assert smoothed.vertices[2, 2] == pytest.approx(raw.vertices[2, 2])


def test_route_top_uses_one_elevation_across_its_exact_width() -> None:
    surface = terrain_surface_from_grid(
        _grid([[100.0, 100.0], [0.0, 0.0]]),
        width_mm=100.0,
        height_mm=100.0,
        horizontal_span_m=1_000.0,
    )
    centerline = LineString([(10.0, 50.0), (90.0, 50.0)])
    route_width = 1.2
    polygon = centerline.buffer(route_width / 2.0, cap_style=2, join_style=1)
    feature = route_mesh_from_polygon(polygon, 2.2, -0.2)
    draped = drape_route_mesh(
        feature,
        centerline,
        surface,
        route_width_mm=route_width,
        visible_height_mm=2.0,
        smoothing_distance_mm=1.5,
    )

    assert polygon.bounds[3] - polygon.bounds[1] == pytest.approx(route_width)
    original_top = np.isclose(feature.vertices[:, 2], 2.0)
    original_bottom = np.isclose(feature.vertices[:, 2], -0.2)
    for endpoint_x in (10.0, 90.0):
        endpoint_top = original_top & np.isclose(feature.vertices[:, 0], endpoint_x)
        endpoint_bottom = original_bottom & np.isclose(feature.vertices[:, 0], endpoint_x)
        assert np.count_nonzero(endpoint_top) == 2
        assert np.ptp(draped.vertices[endpoint_top, 2]) == pytest.approx(0.0, abs=1e-7)
        expected_support = np.max(
            surface.sample(
                np.full(5, endpoint_x),
                np.linspace(50.0 - route_width / 2.0, 50.0 + route_width / 2.0, 5),
            )
        )
        assert draped.vertices[endpoint_top, 2] == pytest.approx(
            expected_support + 2.0
        )
        # The underside still follows the cross-slope and remains embedded in terrain.
        assert np.ptp(draped.vertices[endpoint_bottom, 2]) > 0.0
    assert draped.is_watertight
    assert draped.is_winding_consistent
    assert len(draped.split(only_watertight=False)) == 1


def test_major_road_has_flat_cross_sections_and_directional_smoothing() -> None:
    class RidgeSurface:
        @staticmethod
        def sample(x_mm, y_mm):
            x = np.asarray(x_mm, dtype=float)
            y = np.asarray(y_mm, dtype=float)
            cross_slope = y * 0.01
            sharp_ridge = 1.5 * np.exp(-((x - 50.0) / 1.5) ** 2)
            return cross_slope + sharp_ridge

    centerline = LineString([(10.0, 50.0), (90.0, 50.0)])
    width = 2.4
    region = centerline.buffer(width / 2.0, cap_style=2, join_style=1)
    feature = route_mesh_from_polygon(region, 1.0, -0.2)
    feature = refine_mesh_edges(feature, 2.0, region=region)
    corridor = SimpleNamespace(
        centerline=centerline,
        region=region,
        width_mm=width,
        classification="motorway",
    )

    draped = drape_road_mesh(
        feature,
        RidgeSurface(),
        (corridor,),
        visible_height_mm=0.8,
        minimum_visible_height_mm=0.4,
        smoothing_distances_mm={"motorway": 6.0},
    )

    original_top = np.isclose(feature.vertices[:, 2], 0.8)
    original_bottom = np.isclose(feature.vertices[:, 2], -0.2)
    ridge_top = original_top & np.isclose(feature.vertices[:, 0], 50.0)
    ridge_bottom = original_bottom & np.isclose(feature.vertices[:, 0], 50.0)
    assert np.count_nonzero(ridge_top) >= 2
    assert np.ptp(draped.vertices[ridge_top, 2]) == pytest.approx(0.0, abs=1e-7)
    assert np.ptp(draped.vertices[ridge_bottom, 2]) > 0.0
    local_support = np.max(
        RidgeSurface.sample(
            np.full(5, 50.0),
            np.linspace(50.0 - width / 2.0, 50.0 + width / 2.0, 5),
        )
    )
    raw_top = local_support + 0.8
    assert np.all(draped.vertices[ridge_top, 2] < raw_top)
    assert np.all(draped.vertices[ridge_top, 2] >= local_support + 0.4)
    assert draped.is_watertight
    assert draped.is_winding_consistent


def test_route_refinement_removes_long_flare_faces_without_opening_mesh() -> None:
    centerline = LineString(
        [(5.0, 5.0), (80.0, 5.0), (80.0, 25.0), (10.0, 25.0)]
    )
    route_width = 1.2
    polygon = centerline.buffer(route_width / 2.0, cap_style=2, join_style=1)
    feature = route_mesh_from_polygon(polygon, 2.2, -0.2)
    maximum_edge = 2.4

    assert np.max(feature.edges_unique_length) > 20.0
    refined = refine_mesh_edges(feature, maximum_edge)

    assert np.max(refined.edges_unique_length) <= maximum_edge + 1e-8
    assert refined.bounds[:, :2] == pytest.approx(feature.bounds[:, :2])
    assert refined.is_watertight
    assert refined.is_winding_consistent
    assert len(refined.split(only_watertight=False)) == 1


def test_partial_edge_refinement_returns_watertight_mesh_at_safety_limit() -> None:
    feature = route_mesh_from_polygon(box(0.0, 0.0, 100.0, 2.4), 1.0, -0.2)

    partial = refine_mesh_edges(
        feature,
        1.0,
        maximum_iterations=1,
        allow_partial=True,
    )

    assert partial.metadata["edge_refinement_incomplete"] is True
    assert partial.is_watertight
    assert partial.is_winding_consistent
    with pytest.raises(ValueError, match="Mesh edge refinement exceeded"):
        refine_mesh_edges(feature, 1.0, maximum_iterations=1)


class FixtureProvider:
    name = "fixture"

    def fetch(
        self,
        bounds: tuple[float, float, float, float],
        grid_size: tuple[int, int],
        cache_dir: Path,
    ) -> ElevationGrid:
        south, north, west, east = bounds
        rows, columns = grid_size
        return ElevationGrid(
            np.arange(rows * columns, dtype=float).reshape(rows, columns),
            south,
            north,
            west,
            east,
            self.name,
        )


def test_provider_contract_supports_offline_fixtures(tmp_path: Path) -> None:
    result = FixtureProvider().fetch((29.7, 29.8, -95.4, -95.3), (3, 4), tmp_path)
    assert result.elevations_m.shape == (3, 4)
    assert result.source == "fixture"


class FakeResponse:
    def __init__(self, *, metadata: dict | None = None, content: bytes = b"") -> None:
        self._metadata = metadata
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        assert self._metadata is not None
        return self._metadata


class FakeSession:
    def __init__(self, tiff: bytes) -> None:
        self.tiff = tiff
        self.calls = 0

    def get(self, url: str, **_kwargs) -> FakeResponse:
        self.calls += 1
        if self.calls == 1:
            return FakeResponse(metadata={"href": "https://example.test/elevation.tif"})
        return FakeResponse(content=self.tiff)


def test_usgs_provider_decodes_and_caches_float_dem(tmp_path: Path) -> None:
    values = np.array([[12.5, 13.0], [11.0, 11.5]], dtype=np.float32)
    payload = BytesIO()
    Image.fromarray(values, mode="F").save(payload, format="TIFF")
    session = FakeSession(payload.getvalue())
    provider = Usgs3depProvider(session=session)
    bounds = (29.7, 29.8, -95.4, -95.3)

    first = provider.fetch(bounds, (2, 2), tmp_path)
    second = provider.fetch(bounds, (2, 2), tmp_path)

    assert first.elevations_m == pytest.approx(values)
    assert second.elevations_m == pytest.approx(values)
    assert session.calls == 2  # the second provider call is served entirely from cache


class FlakyTerrainSession(FakeSession):
    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls += 1
        if self.calls == 1:
            return FakeResponse(metadata={"href": "https://example.test/elevation.tif"})
        if self.calls == 2:
            raise requests.ReadTimeout("temporary USGS timeout")
        return FakeResponse(content=self.tiff)


def test_usgs_provider_retries_transient_image_timeout(tmp_path: Path) -> None:
    values = np.array([[4.0, 5.0], [6.0, 7.0]], dtype=np.float32)
    payload = BytesIO()
    Image.fromarray(values, mode="F").save(payload, format="TIFF")
    session = FlakyTerrainSession(payload.getvalue())
    provider = Usgs3depProvider(
        session=session,
        timeout_seconds=1.0,
        max_attempts=3,
        backoff_seconds=0.0,
    )

    result = provider.fetch((35.9, 36.0, -86.9, -86.8), (2, 2), tmp_path)

    assert result.elevations_m == pytest.approx(values)
    assert session.calls == 3


def test_terrarium_decoder_returns_elevation_metres() -> None:
    image = Image.new("RGB", (1, 1), (128, 10, 128))
    assert _decode_terrarium(image)[0, 0] == pytest.approx(10.5)


def test_generation_uses_global_dem_when_usgs_fails(
    tmp_path: Path, monkeypatch
) -> None:
    route = load_route_from_gpx(Path(__file__).parent / "fixtures" / "frame_route.gpx")
    frame = MapFrame.fit_route(route.points, 120.0, 90.0, margin_mm=5.0)

    def fail_usgs(*_args, **_kwargs):
        raise RuntimeError("USGS unavailable")

    def global_grid(_self, bounds, grid_size, _cache_dir):
        south, north, west, east = bounds
        rows, columns = grid_size
        values = np.arange(rows * columns, dtype=float).reshape(rows, columns)
        return ElevationGrid(
            values, south, north, west, east, "aws-terrarium"
        )

    monkeypatch.setattr(Usgs3depProvider, "fetch", fail_usgs)
    monkeypatch.setattr(TerrariumProvider, "fetch", global_grid)
    result = generation.generate_memory_map(
        generation.GenerationRequest(
            route=route,
            frame=frame,
            output_path=tmp_path / "global-fallback.3mf",
            include_route=False,
            include_roads=False,
            include_buildings=False,
            config={"terrain_enabled": True, "terrain_grid_size": 4},
        )
    )

    assert result.stats["terrain"]["source"] == "aws-terrarium"
    assert any("global DEM fallback" in warning for warning in result.warnings)
    assert not any("flat base" in warning for warning in result.warnings)

def test_generation_falls_back_to_flat_terrain_after_usgs_failure(
    tmp_path: Path, monkeypatch
) -> None:
    route = load_route_from_gpx(Path(__file__).parent / "fixtures" / "frame_route.gpx")
    frame = MapFrame.fit_route(route.points, 120.0, 90.0, margin_mm=5.0)

    def fail_fetch(*_args, **_kwargs):
        raise RuntimeError("USGS unavailable")

    monkeypatch.setattr(Usgs3depProvider, "fetch", fail_fetch)
    monkeypatch.setattr(TerrariumProvider, "fetch", fail_fetch)
    result = generation.generate_memory_map(
        generation.GenerationRequest(
            route=route,
            frame=frame,
            output_path=tmp_path / "flat-fallback.3mf",
            include_route=False,
            include_roads=False,
            include_buildings=False,
            config={
                "terrain_enabled": True,
                "terrain_grid_size": 4,
                "terrain_flat_fallback": True,
            },
        )
    )

    assert result.output_path.is_file()
    assert result.stats["terrain"]["source"] == "flat-fallback"
    assert any("using a flat base" in warning for warning in result.warnings)

def test_water_is_clipped_to_the_print_margin() -> None:
    surface = terrain_surface_from_grid(
        _grid([[10.0, 10.0], [10.0, 10.0]]),
        width_mm=100.0,
        height_mm=80.0,
        horizontal_span_m=1_000.0,
    )
    bodies = prepare_water_bodies(
        [box(-10.0, -10.0, 110.0, 90.0)],
        surface,
        margin_mm=5.0,
    )
    water = build_vector_water_mesh(bodies)

    assert water is not None
    assert water.bounds[0, 0] >= 5.0
    assert water.bounds[0, 1] >= 5.0
    assert water.bounds[1, 0] <= 95.0
    assert water.bounds[1, 1] <= 75.0

def test_water_is_recessed_and_exported_as_gray_assembly_part(tmp_path: Path) -> None:
    surface = terrain_surface_from_grid(
        _grid([[30.0, 35.0, 40.0], [25.0, 30.0, 35.0], [20.0, 25.0, 30.0]]),
        width_mm=100.0,
        height_mm=80.0,
        horizontal_span_m=10_000.0,
    )
    bayou = LineString([(-5.0, 20.0), (25.0, 30.0), (55.0, 25.0), (105.0, 55.0)]).buffer(
        4.0, resolution=8
    )
    bodies = prepare_water_bodies([bayou], surface, recess_mm=0.4)
    water = build_vector_water_mesh(bodies, thickness_mm=0.6)
    assert water is not None
    assert len(bodies) == 1
    assert water.is_watertight
    assert len(water.split()) == 1
    assert bodies[0].level_mm <= float(surface.sample(25.0, 30.0)) - 0.4

    output = tmp_path / "terrain-water.3mf"
    terrain = build_terrain_mesh_with_water(surface, 1.0, bodies)
    assert terrain.is_watertight
    assert terrain.is_winding_consistent
    assert terrain.volume > 0.0
    centers = terrain.triangles_center
    upward = terrain.face_normals[:, 2] > 0.9
    inside_water = np.asarray([
        bodies[0].geometry.buffer(-0.01).contains(Point(x, y))
        for x, y in centers[:, :2]
    ])
    support_faces = centers[upward & inside_water]
    assert len(support_faces) > 0
    expected_support = bodies[0].level_mm - 0.2
    assert support_faces[:, 2] == pytest.approx(expected_support)
    assert water.bounds[1, 2] == pytest.approx(bodies[0].level_mm)
    assert water.bounds[1, 2] - support_faces[:, 2].max() == pytest.approx(0.2)
    assert water.bounds[1, 2] > support_faces[:, 2].max()
    export_3mf(output, terrain, None, water_mesh=water)
    with zipfile.ZipFile(output) as archive:
        model_name = next(name for name in archive.namelist() if name.lower().endswith(".model"))
        root = ET.fromstring(archive.read(model_name))
    namespace = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    objects = root.findall("m:resources/m:object", namespace)
    by_name = {item.attrib.get("name"): item for item in objects}
    assert {"Base_White", "Water_Gray", "MemoryMap"} <= set(by_name)
    palettes = {
        item.attrib["id"]: [base.attrib["displaycolor"] for base in item]
        for item in root.findall("m:resources/m:basematerials", namespace)
    }
    water_object = by_name["Water_Gray"]
    assert palettes[water_object.attrib["pid"]][int(water_object.attrib["pindex"])] == "#808080FF"


def test_generation_service_drapes_route_and_exports_recessed_water(tmp_path: Path) -> None:
    route = load_route_from_gpx(Path(__file__).parent / "fixtures" / "frame_route.gpx")
    frame = MapFrame(
        center_lat=29.7600,
        center_lon=-95.3700,
        coverage_width_m=180.0,
        coverage_height_m=140.0,
        print_width_mm=120.0,
        print_height_mm=90.0,
        margin_mm=5.0,
    )
    config = load_config()
    config.update({"terrain_enabled": True, "water_enabled": True})
    output = tmp_path / "terrain-generation.3mf"
    result = generation.generate_memory_map(
        generation.GenerationRequest(
            route=route,
            frame=frame,
            output_path=output,
            include_roads=False,
            include_buildings=True,
            config=config,
            buildings_file=Path(__file__).parent / "fixtures" / "frame_buildings.geojson",
            elevation_grid=_grid(
                [[30.0, 30.3, 30.6], [29.8, 30.1, 30.4], [29.5, 29.8, 30.1]]
            ),
            water_polygons=[box(25.0, 20.0, 75.0, 60.0)],
        )
    )
    assert output.is_file()
    assert result.base_mesh is not None and result.base_mesh.is_watertight
    assert result.route_mesh is not None
    assert result.buildings_mesh is not None
    assert result.water_mesh is not None
    assert result.stats["terrain"]["source"] == "fixture"
    assert result.stats["terrain"]["flatness_rating"] == 5
    assert result.stats["layers"]["water"] is not None
    assert result.buildings_mesh.bounds[0, 2] > -0.2
    assert result.water_mesh.bounds[1, 2] - result.water_mesh.bounds[0, 2] == pytest.approx(0.6)
    assert result.water_mesh.bounds[0, 2] >= -0.6 - 1e-9


def test_water_loader_transforms_local_osm_polygons_into_print_space() -> None:
    frame = MapFrame(
        center_lat=29.7600,
        center_lon=-95.3700,
        coverage_width_m=180.0,
        coverage_height_m=140.0,
        print_width_mm=120.0,
        print_height_mm=90.0,
        margin_mm=5.0,
    )
    polygons = download_water_polygons(
        bbox=(29.759, 29.761, -95.371, -95.369),
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        map_width_mm=frame.print_width_mm,
        map_height_mm=frame.print_height_mm,
        water_file=Path(__file__).parent / "fixtures" / "frame_buildings.geojson",
    )
    assert polygons
    assert all(0.0 <= polygon.bounds[0] <= polygon.bounds[2] <= 120.0 for polygon in polygons)
    assert all(0.0 <= polygon.bounds[1] <= polygon.bounds[3] <= 90.0 for polygon in polygons)


def test_water_tags_include_marine_ocean_features() -> None:
    assert "water" in WATER_TAGS
    assert "place" in WATER_TAGS
    water_values = WATER_TAGS["water"]
    place_values = WATER_TAGS["place"]
    assert isinstance(water_values, list)
    assert isinstance(place_values, list)
    assert "ocean" in water_values
    assert "sea" in water_values
    assert "ocean" in place_values
    assert "sea" in place_values


def test_water_layer_keeps_valid_parts_when_one_polygon_extrusion_fails(monkeypatch) -> None:
    bodies = [
        SimpleNamespace(geometry=box(0.0, 0.0, 10.0, 10.0), level_mm=0.0),
        SimpleNamespace(geometry=box(20.0, 20.0, 30.0, 30.0), level_mm=0.0),
    ]
    calls = {"count": 0}

    def _fake_extrude(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("degenerate polygon")
        return Trimesh(
            vertices=np.array(
                [
                    [0.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0],
                    [1.0, 1.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0, 1.0],
                    [1.0, 0.0, 1.0],
                    [1.0, 1.0, 1.0],
                    [0.0, 1.0, 1.0],
                ],
            ),
            faces=np.array(
                [
                    [0, 2, 1],
                    [0, 3, 2],
                    [4, 5, 6],
                    [4, 6, 7],
                    [0, 1, 5],
                    [0, 5, 4],
                    [1, 2, 6],
                    [1, 6, 5],
                    [2, 3, 7],
                    [2, 7, 6],
                    [3, 0, 4],
                    [3, 4, 7],
                ]
            ),
            process=False,
        )

    monkeypatch.setattr("memorymap_pipeline.water.route_mesh_from_polygon", _fake_extrude)

    mesh = build_vector_water_mesh(bodies, thickness_mm=0.6)

    assert calls["count"] == 2
    assert mesh is not None
    assert len(mesh.faces) > 0
