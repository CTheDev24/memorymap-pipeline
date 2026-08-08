import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import requests
from PIL import Image
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from trimesh import Trimesh

from memorymap_pipeline import generation, water as water_module
from memorymap_pipeline.config import load_config
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.mesh import export_3mf, refine_mesh_edges, route_mesh_from_polygon
from memorymap_pipeline.terrain import (
    ElevationGrid,
    TerrainSurface,
    analyze_terrain,
    build_terrain_mesh,
    drape_mesh,
    drape_road_mesh,
    drape_route_mesh,
    elevation_grid_for_frame,
    terrain_grid_for_print,
    terrain_surface_from_grid,
)
from memorymap_pipeline.terrain_providers import (
    TerrariumProvider,
    Usgs3depProvider,
    _decode_terrarium,
)
from memorymap_pipeline.water import (
    LINEAR_WATERWAY_WIDTHS_MM,
    WATER_TAGS,
    WaterFeature,
    _infer_coastal_water_regions,
    build_terrain_mesh_with_water,
    build_printable_vector_water_mesh,
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


def test_adaptive_terrain_grid_tracks_print_resolution_and_aspect_ratio() -> None:
    grid = terrain_grid_for_print(240.0, 190.0)

    assert grid.mode == "adaptive"
    assert grid.shape == (347, 438)
    assert grid.columns > grid.rows
    assert grid.cell_width_mm == pytest.approx(0.55, abs=0.01)
    assert grid.cell_height_mm == pytest.approx(0.55, abs=0.01)
    assert grid.sample_count <= 180_000
    assert grid.estimated_terrain_faces < 1_000_000


def test_adaptive_terrain_grid_caps_fine_targets_without_becoming_square() -> None:
    grid = terrain_grid_for_print(
        240.0,
        190.0,
        target_cell_size_mm=0.1,
        maximum_samples=50_000,
        maximum_dimension=512,
    )

    assert grid.sample_count <= 50_000
    assert max(grid.shape) <= 512
    assert grid.columns > grid.rows
    assert grid.columns / grid.rows == pytest.approx(240.0 / 190.0, rel=0.02)


def test_explicit_terrain_grid_overrides_remain_exact() -> None:
    square = terrain_grid_for_print(240.0, 190.0, override=96)
    rectangular = terrain_grid_for_print(240.0, 190.0, override=(80, 120))

    assert square.mode == "override"
    assert square.shape == (96, 96)
    assert rectangular.shape == (80, 120)


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


def test_surface_preserves_contours_in_robust_elevation_tails() -> None:
    elevations = np.arange(100.0).reshape(10, 10)
    surface = terrain_surface_from_grid(
        _grid(elevations),
        width_mm=100.0,
        height_mm=100.0,
        horizontal_span_m=10_000.0,
        maximum_relief_mm=12.0,
    )
    ordered = np.sort(surface.heights_mm.reshape(-1))

    assert ordered[0] == pytest.approx(0.0)
    assert ordered[1] > ordered[0]
    assert ordered[-1] == pytest.approx(surface.analysis.target_relief_mm)
    assert ordered[-2] < ordered[-1]


def test_surface_repairs_both_polarities_of_dem_nodata_without_spikes() -> None:
    elevations = np.tile(np.linspace(0.0, 400.0, 9), (9, 1))
    elevations[4, 4] = np.finfo(np.float32).max
    elevations[6:8, 1:3] = -8.0e19
    surface = terrain_surface_from_grid(
        _grid(elevations),
        width_mm=90.0,
        height_mm=90.0,
        horizontal_span_m=10_000.0,
        maximum_relief_mm=12.0,
    )

    assert np.isfinite(surface.heights_mm).all()
    assert surface.heights_mm[4, 4] == pytest.approx(
        np.mean((surface.heights_mm[4, 3], surface.heights_mm[4, 5])),
        abs=0.1,
    )
    assert np.max(np.abs(np.diff(surface.heights_mm, axis=1))) < 3.0


def test_terrain_detail_gamma_expands_lowland_relief_without_moving_peaks() -> None:
    elevations = np.linspace(0.0, 1_000.0, 25).reshape(5, 5)
    linear = terrain_surface_from_grid(
        _grid(elevations),
        width_mm=100.0,
        height_mm=100.0,
        horizontal_span_m=10_000.0,
        maximum_relief_mm=12.0,
        detail_gamma=1.0,
    )
    detailed = terrain_surface_from_grid(
        _grid(elevations),
        width_mm=100.0,
        height_mm=100.0,
        horizontal_span_m=10_000.0,
        maximum_relief_mm=12.0,
        detail_gamma=0.75,
    )

    assert detailed.heights_mm.min() == pytest.approx(linear.heights_mm.min())
    assert detailed.heights_mm.max() == pytest.approx(linear.heights_mm.max())
    assert detailed.heights_mm[1, 0] > linear.heights_mm[1, 0]


def test_geographic_dem_is_cropped_to_the_exact_print_frame() -> None:
    frame = MapFrame(
        center_lat=36.25,
        center_lon=-121.75,
        coverage_width_m=10_000.0,
        coverage_height_m=20_000.0,
        print_width_mm=100.0,
        print_height_mm=200.0,
    )
    _, frame_longitudes = frame.print_to_lonlat(
        np.array([0.0, 100.0]),
        np.array([100.0, 100.0]),
    )
    half_span = float(np.ptp(frame_longitudes)) / 2.0
    source = ElevationGrid(
        np.tile(np.linspace(0.0, 100.0, 5), (5, 1)),
        south=36.0,
        north=36.5,
        west=frame.center_lon - 2.0 * half_span,
        east=frame.center_lon + 2.0 * half_span,
        source="oversized-fixture",
    )

    cropped = elevation_grid_for_frame(source, frame, (5, 5))

    assert cropped.elevations_m[:, 0] == pytest.approx(25.0, abs=0.1)
    assert cropped.elevations_m[:, -1] == pytest.approx(75.0, abs=0.1)
    assert np.ptp(cropped.elevations_m) == pytest.approx(50.0, abs=0.2)


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


def test_terrain_mesh_keeps_the_outer_trim_flat() -> None:
    surface = terrain_surface_from_grid(
        _grid(np.arange(121.0).reshape(11, 11)),
        width_mm=100.0,
        height_mm=80.0,
        horizontal_span_m=10_000.0,
    )
    mesh = build_terrain_mesh(
        surface,
        base_thickness_mm=1.0,
        flat_margin_mm=10.0,
    )
    vertices = mesh.vertices
    top_vertices = vertices[:, 2] > -0.5
    trim_vertices = (
        (vertices[:, 0] <= 10.0 + 1e-8)
        | (vertices[:, 0] >= 90.0 - 1e-8)
        | (vertices[:, 1] <= 10.0 + 1e-8)
        | (vertices[:, 1] >= 70.0 - 1e-8)
    )

    assert mesh.is_watertight
    assert np.max(vertices[top_vertices & trim_vertices, 2]) == pytest.approx(0.0)
    assert np.max(vertices[top_vertices & ~trim_vertices, 2]) > 0.0


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
        # The solid keeps one thickness across the route instead of stretching its
        # low side down the cross-slope into an oversized wall.
        assert np.ptp(draped.vertices[endpoint_bottom, 2]) == pytest.approx(
            0.0, abs=1e-7
        )
        assert np.mean(draped.vertices[endpoint_top, 2]) - np.mean(
            draped.vertices[endpoint_bottom, 2]
        ) == pytest.approx(2.2)
    assert draped.is_watertight
    assert draped.is_winding_consistent
    assert len(draped.split(only_watertight=False)) == 1


def test_route_raises_its_underside_when_bridging_a_short_valley() -> None:
    class ValleySurface:
        @staticmethod
        def sample(x_mm, y_mm):
            x = np.asarray(x_mm, dtype=float)
            return np.where((x >= 45.0) & (x <= 55.0), 0.0, 2.0)

    centerline = LineString([(10.0, 50.0), (90.0, 50.0)])
    width = 1.2
    region = centerline.buffer(width / 2.0, cap_style=2, join_style=1)
    route = refine_mesh_edges(route_mesh_from_polygon(region, 2.0, -0.2), 1.0)
    draped = drape_route_mesh(
        route,
        centerline,
        ValleySurface(),
        route_width_mm=width,
        visible_height_mm=2.0,
        smoothing_distance_mm=0.0,
        maximum_profile_slope=0.1,
    )

    valley = np.isclose(route.vertices[:, 0], 50.0, atol=0.51)
    bottom = valley & np.isclose(route.vertices[:, 2], -0.2)
    top = valley & np.isclose(route.vertices[:, 2], 1.8)
    assert np.min(draped.vertices[bottom, 2]) > 1.0
    assert np.min(draped.vertices[top, 2]) > 3.0
    assert np.mean(draped.vertices[top, 2]) - np.mean(
        draped.vertices[bottom, 2]
    ) == pytest.approx(2.0)
    assert draped.is_watertight


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


def test_usgs_provider_recognizes_positive_and_negative_float_sentinels(
    tmp_path: Path,
) -> None:
    values = np.array(
        [[12.5, np.finfo(np.float32).max], [-8.0e19, 11.5]],
        dtype=np.float32,
    )
    payload = BytesIO()
    Image.fromarray(values, mode="F").save(payload, format="TIFF")
    provider = Usgs3depProvider(session=FakeSession(payload.getvalue()))

    result = provider.fetch((36.0, 36.1, -122.1, -122.0), (2, 2), tmp_path)

    assert np.isnan(result.elevations_m[0, 1])
    assert np.isnan(result.elevations_m[1, 0])
    assert result.elevations_m[0, 0] == pytest.approx(12.5)


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


def test_generation_requests_rectangular_adaptive_grid_and_reports_it(
    tmp_path: Path, monkeypatch
) -> None:
    route = load_route_from_gpx(Path(__file__).parent / "fixtures" / "frame_route.gpx")
    frame = MapFrame.fit_route(route.points, 120.0, 90.0, margin_mm=5.0)
    requested_shapes: list[tuple[int, int]] = []

    def fixture_grid(_self, bounds, grid_size, _cache_dir):
        requested_shapes.append(grid_size)
        south, north, west, east = bounds
        rows, columns = grid_size
        return ElevationGrid(
            np.arange(rows * columns, dtype=float).reshape(rows, columns),
            south,
            north,
            west,
            east,
            "fixture",
        )

    monkeypatch.setattr(Usgs3depProvider, "fetch", fixture_grid)
    result = generation.generate_memory_map(
        generation.GenerationRequest(
            route=route,
            frame=frame,
            output_path=tmp_path / "adaptive-terrain.3mf",
            include_route=False,
            include_roads=False,
            include_buildings=False,
            config={
                "terrain_enabled": True,
                "terrain_target_cell_size_mm": 10.0,
            },
        )
    )

    assert requested_shapes == [(10, 13)]
    diagnostics = result.stats["terrain"]["grid"]
    assert diagnostics["mode"] == "adaptive"
    assert (diagnostics["rows"], diagnostics["columns"]) == (10, 13)
    assert diagnostics["samples"] == 130
    assert diagnostics["estimated_terrain_faces"] == 516


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


def test_jagged_coastline_partition_keeps_structural_base_manifold() -> None:
    surface = terrain_surface_from_grid(
        _grid(np.arange(64, dtype=float).reshape(8, 8)),
        width_mm=240.0,
        height_mm=190.0,
        horizontal_span_m=35_000.0,
    )
    coastline = LineString(
        [
            (35.0, -10.0),
            (41.00000004, 18.0),
            (39.99999996, 42.0),
            (72.0, 61.00000003),
            (70.0, 94.99999997),
            (108.00000004, 123.0),
            (105.99999996, 151.0),
            (132.0, 200.0),
        ]
    )
    ocean = box(-20.0, -20.0, 260.0, 210.0).difference(
        Polygon([(x, y) for x, y in coastline.coords] + [(260.0, 210.0), (260.0, -20.0)])
    )
    bodies = prepare_water_bodies([ocean], surface, shoreline_tolerance_mm=0.1)
    terrain = build_terrain_mesh_with_water(surface, 1.6, bodies)
    edge_counts = np.bincount(terrain.edges_unique_inverse)

    assert np.count_nonzero(edge_counts == 1) == 0
    assert np.count_nonzero(edge_counts > 2) == 0
    assert terrain.is_watertight


def test_unprintable_water_body_is_not_recessed_into_terrain(monkeypatch) -> None:
    surface = terrain_surface_from_grid(
        _grid([[20.0, 20.0], [20.0, 20.0]]),
        width_mm=100.0,
        height_mm=80.0,
        horizontal_span_m=10_000.0,
    )
    bodies = prepare_water_bodies(
        [box(10.0, 10.0, 30.0, 30.0), box(60.0, 10.0, 80.0, 30.0)],
        surface,
    )
    rejected = max(bodies, key=lambda body: body.geometry.bounds[0])
    original = water_module.route_mesh_from_polygon

    def selective_extrusion(polygon, height_mm, z_offset=0.0):
        if polygon.equals(rejected.geometry):
            raise ValueError("synthetic non-watertight polygon")
        return original(polygon, height_mm, z_offset)

    monkeypatch.setattr(water_module, "route_mesh_from_polygon", selective_extrusion)

    water_mesh, printable = build_printable_vector_water_mesh(bodies)
    terrain = build_terrain_mesh_with_water(surface, 1.0, printable)

    assert water_mesh is not None and water_mesh.is_watertight
    assert len(printable) == 1
    assert rejected not in printable
    assert terrain.is_watertight


def test_landscape_water_level_is_measured_from_finished_green_surface() -> None:
    surface = terrain_surface_from_grid(
        _grid([[20.0, 20.0], [20.0, 20.0]]),
        width_mm=100.0,
        height_mm=80.0,
        horizontal_span_m=10_000.0,
    )
    lake = box(20.0, 20.0, 80.0, 60.0)
    body = prepare_water_bodies(
        [lake],
        surface,
        recess_mm=0.4,
        surface_offset_mm=0.4,
    )[0]

    terrain_height = float(surface.sample(20.0, 20.0))
    assert body.level_mm == pytest.approx(terrain_height)
    assert terrain_height + 0.4 - body.level_mm == pytest.approx(0.4)


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


def test_generation_replaces_coastal_base_with_exactly_twelve_open_edges(
    monkeypatch, tmp_path: Path
) -> None:
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
    broken_parts = []
    for offset in (5.0, 25.0, 45.0, 65.0):
        part = route_mesh_from_polygon(box(offset, 5.0, offset + 10.0, 15.0), 2.0)
        part.update_faces(np.arange(len(part.faces)) != 0)
        part.remove_unreferenced_vertices()
        broken_parts.append(part)
    from trimesh.util import concatenate

    broken = concatenate(broken_parts)
    edge_counts = np.bincount(broken.edges_unique_inverse)
    assert np.count_nonzero(edge_counts == 1) == 12
    monkeypatch.setattr(generation, "build_terrain_mesh_with_water", lambda *_a, **_k: broken)

    result = generation.generate_memory_map(
        generation.GenerationRequest(
            route=route,
            frame=frame,
            output_path=tmp_path / "coastal-fallback.3mf",
            include_route=False,
            include_roads=False,
            include_buildings=False,
            config={"terrain_enabled": True, "water_enabled": True},
            elevation_grid=_grid(
                [[30.0, 30.3, 30.6], [29.8, 30.1, 30.4], [29.5, 29.8, 30.1]]
            ),
            water_polygons=[box(25.0, 20.0, 75.0, 60.0)],
        )
    )

    repaired_counts = np.bincount(result.base_mesh.edges_unique_inverse)
    assert np.count_nonzero(repaired_counts == 1) == 0
    assert np.count_nonzero(repaired_counts > 2) == 0
    assert result.base_mesh.is_watertight
    assert result.water_mesh is not None
    vertices = result.base_mesh.vertices
    safely_inside_water = np.asarray(
        [box(27.0, 22.0, 73.0, 58.0).contains(Point(x, y)) for x, y in vertices[:, :2]]
    )
    water_support = vertices[
        safely_inside_water & (vertices[:, 2] > result.base_mesh.bounds[0, 2] + 1e-8),
        2,
    ]
    assert len(water_support) > 0
    assert water_support == pytest.approx(result.water_mesh.bounds[1, 2] - 0.2)
    assert water_support.max() < result.water_mesh.bounds[1, 2]
    assert not any("non-manifold structural base" in warning for warning in result.warnings)


def test_hydroflattened_water_level_is_restored_only_on_mapped_land() -> None:
    heights = np.tile(np.arange(10, dtype=float), (10, 1))
    heights[:, :6] = 0.0
    surface = TerrainSurface(
        heights_mm=heights,
        width_mm=90.0,
        height_mm=90.0,
        analysis=analyze_terrain(
            np.asarray([[0.0, 10.0], [0.0, 10.0]]), 100.0
        ),
    )

    restored, count = generation._restore_hydroflattened_land(
        surface, [box(0.0, 0.0, 39.9, 90.0)]
    )

    assert count == 20
    assert restored.heights_mm[:, :4] == pytest.approx(0.0)
    assert np.all(restored.heights_mm[:, 4:6] > 0.0)
    assert restored.heights_mm[:, 6:] == pytest.approx(surface.heights_mm[:, 6:])


def test_landscape_generation_builds_supported_bone_green_blue_layers(
    tmp_path: Path,
) -> None:
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
    water_polygon = box(25.0, 20.0, 75.0, 60.0)
    result = generation.generate_memory_map(
        generation.GenerationRequest(
            route=route,
            frame=frame,
            output_path=tmp_path / "landscape-generation.3mf",
            include_route=False,
            include_roads=False,
            include_buildings=False,
            config={
                "terrain_enabled": True,
                "water_enabled": True,
                "style_profile": "landscape",
                "exposed_land_enabled": False,
                "flat_border_enabled": True,
            },
            elevation_grid=_grid(
                [[30.0, 30.3, 30.6], [29.8, 30.1, 30.4], [29.5, 29.8, 30.1]]
            ),
            water_polygons=[water_polygon],
        )
    )

    assert result.base_mesh is not None and result.base_mesh.is_watertight
    assert result.landscape_mesh is not None and result.landscape_mesh.is_watertight
    assert result.water_mesh is not None and result.water_mesh.is_watertight
    assert result.landscape_mesh.bounds[0, 0] >= frame.margin_mm - 1e-8
    assert result.landscape_mesh.bounds[0, 1] >= frame.margin_mm - 1e-8
    assert (
        result.landscape_mesh.bounds[1, 0]
        <= frame.print_width_mm - frame.margin_mm + 1e-8
    )
    assert (
        result.landscape_mesh.bounds[1, 1]
        <= frame.print_height_mm - frame.margin_mm + 1e-8
    )
    base_vertices = result.base_mesh.vertices
    trim_vertices = (
        (base_vertices[:, 0] <= frame.margin_mm + 1e-8)
        | (
            base_vertices[:, 0]
            >= frame.print_width_mm - frame.margin_mm - 1e-8
        )
        | (base_vertices[:, 1] <= frame.margin_mm + 1e-8)
        | (
            base_vertices[:, 1]
            >= frame.print_height_mm - frame.margin_mm - 1e-8
        )
    )
    trim_top = trim_vertices & (
        base_vertices[:, 2] > result.base_mesh.bounds[0, 2] + 1e-8
    )
    assert base_vertices[trim_top, 2] == pytest.approx(0.0)
    centers = result.base_mesh.triangles_center
    upward = result.base_mesh.face_normals[:, 2] > 0.9
    inside_water = np.asarray(
        [
            water_polygon.buffer(-0.01).contains(Point(x, y))
            for x, y in centers[:, :2]
        ]
    )
    support_faces = centers[upward & inside_water]
    assert len(support_faces) > 0
    assert result.water_mesh.bounds[1, 2] - support_faces[:, 2].max() == pytest.approx(
        0.4
    )

    with zipfile.ZipFile(result.output_path) as archive:
        model_name = next(
            name for name in archive.namelist() if name.lower().endswith(".model")
        )
        root = ET.fromstring(archive.read(model_name))
    namespace = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    names = {
        item.attrib.get("name")
        for item in root.findall("m:resources/m:object", namespace)
    }
    assert {"Base_Bone", "Terrain_Green", "Water_Blue", "MemoryMap"} <= names


def test_borderless_generation_contours_terrain_to_plate_extents(
    tmp_path: Path,
) -> None:
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
    result = generation.generate_memory_map(
        generation.GenerationRequest(
            route=route,
            frame=frame,
            output_path=tmp_path / "borderless-terrain.3mf",
            include_route=False,
            include_roads=False,
            include_buildings=False,
            config={
                "terrain_enabled": True,
                "water_enabled": False,
                "flat_border_enabled": False,
            },
            elevation_grid=_grid(
                [[10.0, 30.0, 50.0], [20.0, 40.0, 60.0], [30.0, 50.0, 70.0]]
            ),
        )
    )

    assert result.base_mesh is not None and result.base_mesh.is_watertight
    assert result.stats["flat_border_enabled"] is False
    vertices = result.base_mesh.vertices
    edge = (
        np.isclose(vertices[:, 0], 0.0)
        | np.isclose(vertices[:, 0], frame.print_width_mm)
        | np.isclose(vertices[:, 1], 0.0)
        | np.isclose(vertices[:, 1], frame.print_height_mm)
    )
    edge_top = edge & (
        vertices[:, 2] > result.base_mesh.bounds[0, 2] + 1e-8
    )
    assert np.ptp(vertices[edge_top, 2]) > 0.0


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
    assert "coastline" in WATER_TAGS["natural"]
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
    assert set(("river", "stream", "canal", "drain", "ditch")) <= set(
        WATER_TAGS["waterway"]
    )


def test_water_loader_buffers_local_linear_waterways_to_printable_widths() -> None:
    frame = MapFrame(
        center_lat=29.7600,
        center_lon=-95.3700,
        coverage_width_m=180.0,
        coverage_height_m=140.0,
        print_width_mm=120.0,
        print_height_mm=90.0,
        margin_mm=5.0,
    )

    features = download_water_polygons(
        bbox=(29.759, 29.761, -95.371, -95.369),
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        map_width_mm=frame.print_width_mm,
        map_height_mm=frame.print_height_mm,
        water_file=Path(__file__).parent / "fixtures" / "frame_water.geojson",
        include_metadata=True,
    )

    assert all(isinstance(feature, WaterFeature) for feature in features)
    areas = [feature for feature in features if feature.kind == "area"]
    waterways = {
        feature.waterway: feature
        for feature in features
        if feature.kind == "waterway"
    }
    assert len(areas) == 1
    assert set(waterways) == {"stream", "canal"}
    assert waterways["stream"].geometry.bounds[3] - waterways["stream"].geometry.bounds[1] == pytest.approx(
        LINEAR_WATERWAY_WIDTHS_MM["stream"],
        abs=0.02,
    )
    assert waterways["canal"].geometry.bounds[2] - waterways["canal"].geometry.bounds[0] == pytest.approx(
        LINEAR_WATERWAY_WIDTHS_MM["canal"],
        abs=0.02,
    )
    assert all(feature.geometry.geom_type in ("Polygon", "MultiPolygon") for feature in features)
    assert all(
        0.0 <= feature.geometry.bounds[0] <= feature.geometry.bounds[2] <= 120.0
        for feature in features
    )
    assert all(
        0.0 <= feature.geometry.bounds[1] <= feature.geometry.bounds[3] <= 90.0
        for feature in features
    )


def test_water_loader_honors_minimum_printable_waterway_width() -> None:
    frame = MapFrame(
        center_lat=29.7600,
        center_lon=-95.3700,
        coverage_width_m=180.0,
        coverage_height_m=140.0,
        print_width_mm=120.0,
        print_height_mm=90.0,
        margin_mm=5.0,
    )

    features = download_water_polygons(
        bbox=(29.759, 29.761, -95.371, -95.369),
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        map_width_mm=frame.print_width_mm,
        map_height_mm=frame.print_height_mm,
        water_file=Path(__file__).parent / "fixtures" / "frame_water.geojson",
        minimum_waterway_width_mm=1.4,
        include_metadata=True,
    )

    waterways = [feature for feature in features if feature.kind == "waterway"]
    assert len(waterways) == 2
    assert all(
        min(
            feature.geometry.bounds[2] - feature.geometry.bounds[0],
            feature.geometry.bounds[3] - feature.geometry.bounds[1],
        )
        == pytest.approx(1.4, abs=0.02)
        for feature in waterways
    )


def test_coastline_inference_adds_ocean_region_touching_frame_edge() -> None:
    frame = box(0.0, 0.0, 100.0, 80.0)
    coastline = LineString([(100.0, 50.0), (0.0, 50.0)])

    inferred = _infer_coastal_water_regions([coastline], frame)

    assert inferred
    ocean_union = unary_union(inferred)
    assert ocean_union.contains(Point(50.0, 70.0))
    assert not ocean_union.contains(Point(50.0, 30.0))


def test_coastline_inference_respects_osm_coastline_direction() -> None:
    frame = box(0.0, 0.0, 100.0, 80.0)
    coastline = LineString([(0.0, 50.0), (100.0, 50.0)])

    ocean_union = unary_union(
        _infer_coastal_water_regions([coastline], frame)
    )

    assert ocean_union.contains(Point(50.0, 30.0))
    assert not ocean_union.contains(Point(50.0, 70.0))


def test_coastline_inference_ignores_line_that_does_not_split_frame() -> None:
    frame = box(0.0, 0.0, 100.0, 80.0)
    coastline = LineString([(20.0, 50.0), (80.0, 50.0)])

    assert _infer_coastal_water_regions([coastline], frame) == []


def test_coastline_inference_keeps_closed_island_land_dry() -> None:
    frame = box(0.0, 0.0, 100.0, 80.0)
    coastline = LineString(
        [(30.0, 20.0), (70.0, 20.0), (70.0, 60.0), (30.0, 60.0), (30.0, 20.0)]
    )

    ocean = unary_union(_infer_coastal_water_regions([coastline], frame))

    assert ocean.contains(Point(10.0, 10.0))
    assert not ocean.contains(Point(50.0, 40.0))
    assert ocean.area == pytest.approx(frame.area - 1_600.0)


def test_coastline_inference_uses_dominant_directional_evidence() -> None:
    frame = box(0.0, 0.0, 100.0, 80.0)
    coastline = LineString([(100.0, 50.0), (0.0, 50.0)])
    short_reversed_fragment = LineString([(45.0, 20.0), (55.0, 20.0)])

    ocean = unary_union(
        _infer_coastal_water_regions(
            [coastline, short_reversed_fragment],
            frame,
        )
    )

    assert ocean.contains(Point(50.0, 70.0))
    assert not ocean.contains(Point(50.0, 30.0))


def test_water_loader_fills_ocean_from_oriented_osm_coastline(monkeypatch) -> None:
    import geopandas as gpd
    import osmnx as ox

    frame = MapFrame(
        center_lat=29.0,
        center_lon=-95.0,
        coverage_width_m=2_000.0,
        coverage_height_m=2_000.0,
        print_width_mm=100.0,
        print_height_mm=80.0,
        margin_mm=5.0,
    )
    features = gpd.GeoDataFrame(
        {"natural": ["coastline"]},
        geometry=[LineString([(-95.0, 29.02), (-95.0, 28.98)])],
        crs="EPSG:4326",
    )
    calls = []

    def fetch_features(*_args, **kwargs):
        calls.append(kwargs["tags"])
        return features

    monkeypatch.setattr(ox, "features_from_point", fetch_features)

    polygons = download_water_polygons(
        bbox=(28.98, 29.02, -95.01, -94.99),
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        map_width_mm=frame.print_width_mm,
        map_height_mm=frame.print_height_mm,
        radius_m=1_500.0,
    )

    ocean = unary_union(polygons)
    assert len(calls) == 1
    assert ocean.contains(Point(20.0, 40.0))
    assert not ocean.contains(Point(80.0, 40.0))


def test_water_loader_retries_large_failed_query_with_coastline_bbox(monkeypatch) -> None:
    import geopandas as gpd
    import osmnx as ox

    frame = MapFrame(
        center_lat=36.4,
        center_lon=-121.86,
        coverage_width_m=44_000.0,
        coverage_height_m=35_000.0,
        print_width_mm=240.0,
        print_height_mm=190.0,
        margin_mm=0.0,
    )
    coastline = gpd.GeoDataFrame(
        {"natural": ["coastline"]},
        geometry=[LineString([(-121.86, 36.2), (-121.86, 36.6)])],
        crs="EPSG:4326",
    )
    bbox_calls = []

    def failed_point_query(*_args, **_kwargs):
        raise RuntimeError("synthetic Overpass timeout")

    def coastline_bbox_query(*_args, **kwargs):
        bbox_calls.append(kwargs["tags"])
        return coastline

    monkeypatch.setattr(ox, "features_from_point", failed_point_query)
    monkeypatch.setattr(ox, "features_from_bbox", coastline_bbox_query)

    polygons = download_water_polygons(
        bbox=(36.2, 36.6, -122.1, -121.6),
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        map_width_mm=frame.print_width_mm,
        map_height_mm=frame.print_height_mm,
        radius_m=28_000.0,
    )

    assert bbox_calls == [{"natural": "coastline"}]
    assert polygons


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
