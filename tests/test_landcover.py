import numpy as np
import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from memorymap_pipeline.landcover import (
    InMemoryLandCoverProvider,
    LandCoverClass,
    LandCoverGrid,
    LandCoverProvenance,
    LandCoverRequest,
    GroundCoverCleanupPreset,
    cleanup_exposed_mask,
    coastal_distance_mask,
    exposed_mask,
    fuse_coastal_evidence,
    fuse_land_cover,
    polygonize_exposed_mask,
    rasterize_geometry_mask,
    resample_land_cover,
    resolve_ground_cover_cleanup_preset,
)


def _grid(values: list[list[int]]) -> LandCoverGrid:
    return LandCoverGrid(
        np.asarray(values, dtype=np.uint8),
        (0.0, 0.0, 4.0, 3.0),
        LandCoverProvenance("fixture", "synthetic", "1", cached=True),
    )


def test_fusion_precedence() -> None:
    grid = _grid([[0, 1, 3, 0], [4, 1, 7, 0], [5, 6, 2, 0]])
    osm_exposed = np.zeros(grid.shape, dtype=bool)
    osm_exposed[0, 1] = True
    mapped_water = np.zeros(grid.shape, dtype=bool)
    mapped_water[0, 1] = mapped_water[1, 0] = True
    fused = fuse_land_cover(grid, osm_exposed=osm_exposed, mapped_water=mapped_water)
    assert fused.classes[0, 1] == LandCoverClass.WATER
    assert fused.classes[1, 0] == LandCoverClass.WATER
    assert fused.classes[0, 2] == LandCoverClass.SAND
    assert fused.classes[1, 2] == LandCoverClass.MUD
    assert fused.provenance.details["source_provider"] == "fixture"


def test_in_memory_provider_requires_exact_request() -> None:
    grid = _grid([[0] * 4] * 3)
    provider = InMemoryLandCoverProvider(grid)
    assert provider.get_land_cover(LandCoverRequest(grid.bounds_mm, 3, 4)) is grid
    with pytest.raises(ValueError, match="does not match"):
        provider.get_land_cover(LandCoverRequest(grid.bounds_mm, 6, 8))


def test_exposed_mask_excludes_water_and_vegetation() -> None:
    grid = _grid([[2, 3, 4, 0], [5, 6, 7, 0], [8, 1, 0, 0]])
    mask = exposed_mask(grid)
    assert mask.sum() == 6
    assert not mask[2, 0]
    assert not mask[2, 1]


def test_cleanup_removes_tiny_islands_but_preserves_long_narrow_features() -> None:
    values = np.zeros((10, 50), dtype=np.uint8)
    values[2, 2] = LandCoverClass.BARE
    values[6, 10:35] = LandCoverClass.ROCK
    grid = LandCoverGrid(
        values,
        (0.0, 0.0, 5.0, 1.0),
        LandCoverProvenance("fixture", "synthetic"),
    )

    cleaned = cleanup_exposed_mask(grid, sensitivity="balanced")

    assert not cleaned[2, 2]
    assert cleaned[6, 10:35].all()


def test_cleanup_fills_tiny_holes_but_never_crosses_excluded_water() -> None:
    values = np.full((20, 20), LandCoverClass.BARE, dtype=np.uint8)
    values[5, 5] = LandCoverClass.UNKNOWN
    values[10, 10] = LandCoverClass.WATER
    grid = LandCoverGrid(
        values,
        (0.0, 0.0, 2.0, 2.0),
        LandCoverProvenance("fixture", "synthetic"),
    )
    excluded = np.zeros(grid.shape, dtype=bool)
    excluded[12, 12] = True

    cleaned = cleanup_exposed_mask(
        grid,
        excluded_water=excluded,
        sensitivity="broad",
    )

    assert cleaned[5, 5]
    assert not cleaned[10, 10]
    assert not cleaned[12, 12]


def test_cleanup_sensitivity_presets_are_ordered_and_customizable() -> None:
    conservative = resolve_ground_cover_cleanup_preset("conservative")
    balanced = resolve_ground_cover_cleanup_preset("balanced")
    broad = resolve_ground_cover_cleanup_preset("broad")

    assert conservative.minimum_island_area_mm2 > balanced.minimum_island_area_mm2
    assert balanced.minimum_island_area_mm2 > broad.minimum_island_area_mm2
    assert conservative.maximum_hole_area_mm2 < balanced.maximum_hole_area_mm2
    assert balanced.maximum_hole_area_mm2 < broad.maximum_hole_area_mm2
    custom = GroundCoverCleanupPreset(0.1, 3.0, 0.2, 0)
    assert resolve_ground_cover_cleanup_preset(custom) is custom
    with pytest.raises(ValueError, match="Unsupported ground-cover sensitivity"):
        resolve_ground_cover_cleanup_preset("extreme")


def test_polygonization_dissolves_cells_and_filters_islands() -> None:
    grid = _grid([[2, 2, 0, 0], [2, 2, 0, 0], [0, 0, 0, 3]])
    assert polygonize_exposed_mask(grid).area == pytest.approx(5.0)
    filtered = polygonize_exposed_mask(grid, minimum_area_mm2=2.0)
    assert filtered.area == pytest.approx(4.0)
    assert filtered.geom_type == "Polygon"


def test_polygonization_filters_features_narrower_than_print_width() -> None:
    grid = _grid([[2, 2, 0, 0], [2, 2, 0, 0], [0, 0, 0, 3]])
    assert polygonize_exposed_mask(grid, minimum_width_mm=1.5).area == pytest.approx(4.0)


def test_grid_rejects_unknown_categories_and_copies_input() -> None:
    values = np.zeros((3, 4), dtype=np.uint8)
    grid = LandCoverGrid(values, (0, 0, 4, 3), LandCoverProvenance("x", "y"))
    values[0, 0] = LandCoverClass.WATER
    assert grid.classes[0, 0] == LandCoverClass.UNKNOWN
    with pytest.raises(ValueError, match="unknown class"):
        _grid([[99, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]])


def test_mask_shapes_are_validated() -> None:
    grid = _grid([[0] * 4] * 3)
    with pytest.raises(ValueError, match="shape"):
        fuse_land_cover(grid, mapped_water=np.zeros((2, 2), dtype=bool))


def test_geometry_rasterization_uses_cell_centers_and_preserves_holes() -> None:
    grid = _grid([[0] * 4] * 3)
    polygon = Polygon(
        [(0, 0), (4, 0), (4, 3), (0, 3)],
        holes=[[(1, 1), (3, 1), (3, 2), (1, 2)]],
    )
    mask = rasterize_geometry_mask(grid, polygon)
    assert mask.shape == grid.shape
    assert mask.sum() == 10
    assert not mask[1, 1]
    assert not mask[1, 2]


def test_geometry_rasterization_handles_multipolygons_bounds_and_empty_input() -> None:
    grid = _grid([[0] * 4] * 3)
    geometry = MultiPolygon([box(-5, -5, 1, 1), box(3, 2, 10, 10)])
    mask = rasterize_geometry_mask(grid, geometry)
    assert np.array_equal(
        mask,
        np.array([[True, False, False, False], [False] * 4, [False, False, False, True]]),
    )
    assert not rasterize_geometry_mask(grid, None).any()
    assert not rasterize_geometry_mask(grid, []).any()
    assert not rasterize_geometry_mask(grid, Polygon()).any()


def test_geometry_rasterization_rejects_non_geometry_iterable_members() -> None:
    grid = _grid([[0] * 4] * 3)
    with pytest.raises(TypeError, match="Shapely"):
        rasterize_geometry_mask(grid, [box(0, 0, 1, 1), "not geometry"])  # type: ignore[list-item]


def test_resampling_nearest_neighbor_preserves_categories_and_exact_request() -> None:
    source = LandCoverGrid(
        np.array([[LandCoverClass.BARE, LandCoverClass.WATER], [1, 3]], dtype=np.uint8),
        (0, 0, 2, 2),
        LandCoverProvenance("fixture", "synthetic"),
    )
    result = resample_land_cover(source, LandCoverRequest((0, 0, 2, 2), 4, 4))
    assert result.shape == (4, 4)
    assert np.array_equal(result.classes[:2, :2], np.full((2, 2), LandCoverClass.BARE))
    assert np.array_equal(result.classes[:2, 2:], np.full((2, 2), LandCoverClass.WATER))
    assert result.provenance.details["resampling"] == "nearest-cell-center"


def test_resampling_marks_target_centers_outside_source_unknown() -> None:
    source = LandCoverGrid(
        np.full((2, 2), LandCoverClass.VEGETATION, dtype=np.uint8),
        (0, 0, 2, 2),
        LandCoverProvenance("fixture", "synthetic"),
    )
    result = resample_land_cover(source, LandCoverRequest((-1, -1, 3, 3), 4, 4))
    expected = np.zeros((4, 4), dtype=np.uint8)
    expected[1:3, 1:3] = LandCoverClass.VEGETATION
    assert np.array_equal(result.classes, expected)


def test_coastal_distance_uses_physical_cell_dimensions() -> None:
    water = np.zeros((5, 7), dtype=bool)
    water[2, 3] = True
    coastal = coastal_distance_mask(water, cell_size_m=(100, 250), maximum_distance_m=210)
    assert coastal[2, 1:6].all()
    assert not coastal[1, 3]
    assert not coastal[0, 3]


def test_coastal_scoring_ignores_inland_water_components() -> None:
    values = np.full((20, 20), LandCoverClass.UNKNOWN, dtype=np.uint8)
    values[0:3, 0:3] = LandCoverClass.WATER
    values[10:12, 10:12] = LandCoverClass.WATER
    wc = LandCoverGrid(values, (0, 0, 20, 20), LandCoverProvenance("wc", "wc"))
    io = LandCoverGrid(
        np.zeros((20, 20), dtype=np.uint8),
        (0, 0, 20, 20),
        LandCoverProvenance("io", "io"),
    )

    result = fuse_coastal_evidence(
        wc,
        io,
        cell_size_m=(100, 100),
        coastal_distance_m=150,
        sensitivity="balanced",
    )

    assert result.contribution_masks["coastal_water"][0:3, 0:3].all()
    assert not result.contribution_masks["coastal_water"][10:12, 10:12].any()
    assert not result.contribution_masks["coastal"][10, 10]


def test_evidence_scoring_thresholds_and_contributions() -> None:
    wc_values = np.full((3, 5), LandCoverClass.UNKNOWN, dtype=np.uint8)
    io_values = wc_values.copy()
    wc_values[1, 0:3] = LandCoverClass.BARE
    io_values[1, 2:4] = LandCoverClass.BARE
    water = np.zeros((3, 5), dtype=bool)
    water[0, :] = True
    wc = LandCoverGrid(wc_values, (0, 0, 5, 3), LandCoverProvenance("wc", "WorldCover", "v200"))
    io = LandCoverGrid(io_values, (0, 0, 5, 3), LandCoverProvenance("io", "IO", "2023"))

    balanced = fuse_coastal_evidence(
        wc, io, mapped_water=water, cell_size_m=(10, 10), sensitivity="balanced"
    )
    # Coastal and connected evidence lift either provider's bare classification
    # over Balanced's threshold; the overlap has both source contributions.
    assert exposed_mask(balanced.grid)[1, :4].all()
    assert balanced.scores[1, 2] == 120
    assert balanced.contribution_counts["worldcover_bare"] == 3
    assert balanced.contribution_counts["impact_bare"] == 2
    assert balanced.grid.provenance.details["threshold"] == "55"
    assert not balanced.scores.flags.writeable
    assert not balanced.contribution_masks["coastal"].flags.writeable

    conservative = fuse_coastal_evidence(
        wc, io, mapped_water=water, cell_size_m=(10, 10), sensitivity="conservative"
    )
    assert not exposed_mask(conservative.grid)[1, 0]
    assert exposed_mask(conservative.grid)[1, 2]


def test_built_is_rejected_unless_osm_explicit_and_water_always_wins() -> None:
    wc = _grid([[9, 9, 2, 0], [0] * 4, [0] * 4])
    io = _grid([[2, 2, 2, 0], [0] * 4, [0] * 4])
    explicit = np.zeros(wc.shape, dtype=bool)
    explicit[0, 1] = True
    explicit[0, 2] = True
    water = np.zeros(wc.shape, dtype=bool)
    water[0, 2] = True
    result = fuse_coastal_evidence(
        wc,
        io,
        osm_exposed=explicit,
        mapped_water=water,
        cell_size_m=(10, 10),
        sensitivity="broad",
    )
    assert result.grid.classes[0, 0] == LandCoverClass.BUILT
    assert result.grid.classes[0, 1] == LandCoverClass.BARE
    assert result.grid.classes[0, 2] == LandCoverClass.WATER


def test_strong_vegetation_penalty_and_cliff_support() -> None:
    wc = _grid([[2, 2, 0, 0], [0] * 4, [0] * 4])
    io = _grid([[0] * 4, [0] * 4, [0] * 4])
    vegetation = np.zeros(wc.shape, dtype=bool)
    vegetation[0, 0] = True
    cliff = np.zeros(wc.shape, dtype=bool)
    cliff[0, 1] = True
    result = fuse_coastal_evidence(
        wc,
        io,
        cliff_or_outcrop=cliff,
        strong_vegetation=vegetation,
        cell_size_m=(10, 10),
        sensitivity="broad",
    )
    assert not exposed_mask(result.grid)[0, 0]
    assert exposed_mask(result.grid)[0, 1]
