"""Categorical land-cover primitives used by landscape surface generation.

This module deliberately has no network or raster-file dependency.  Providers
return a grid already sampled in print coordinates; callers can therefore use
the same fusion and polygonization path for downloaded data and offline tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from collections.abc import Iterable
import heapq
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray
from shapely import contains_xy
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


class LandCoverClass(IntEnum):
    """Normalized land-cover classes, ordered independently of precedence."""

    UNKNOWN = 0
    VEGETATION = 1
    BARE = 2
    SAND = 3
    ROCK = 4
    SCREE = 5
    SHINGLE = 6
    MUD = 7
    WATER = 8
    BUILT = 9


EXPOSED_CLASSES = frozenset(
    {
        LandCoverClass.BARE,
        LandCoverClass.SAND,
        LandCoverClass.ROCK,
        LandCoverClass.SCREE,
        LandCoverClass.SHINGLE,
        LandCoverClass.MUD,
    }
)


@dataclass(frozen=True)
class GroundCoverCleanupPreset:
    """Print-space thresholds for exposed-ground mask cleanup."""

    minimum_island_area_mm2: float
    preserve_narrow_span_mm: float
    maximum_hole_area_mm2: float
    smoothing_passes: int


GROUND_COVER_CLEANUP_PRESETS = {
    "conservative": GroundCoverCleanupPreset(0.8, 2.5, 0.25, 0),
    "balanced": GroundCoverCleanupPreset(0.5, 2.0, 0.5, 1),
    "broad": GroundCoverCleanupPreset(0.25, 1.5, 1.0, 1),
}


def resolve_ground_cover_cleanup_preset(
    sensitivity: str | GroundCoverCleanupPreset,
) -> GroundCoverCleanupPreset:
    """Resolve a named cleanup sensitivity or return an explicit preset."""

    if isinstance(sensitivity, GroundCoverCleanupPreset):
        preset = sensitivity
    else:
        try:
            preset = GROUND_COVER_CLEANUP_PRESETS[sensitivity]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported ground-cover sensitivity: {sensitivity}"
            ) from exc
    if (
        preset.minimum_island_area_mm2 < 0
        or preset.preserve_narrow_span_mm < 0
        or preset.maximum_hole_area_mm2 < 0
        or preset.smoothing_passes < 0
    ):
        raise ValueError("ground-cover cleanup thresholds must be non-negative")
    return preset


@dataclass(frozen=True)
class LandCoverProvenance:
    """Identifies the data and cache state used to produce a grid."""

    provider: str
    dataset: str
    edition: str | None = None
    cached: bool = False
    details: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LandCoverRequest:
    """A regular print-space sampling request."""

    bounds_mm: tuple[float, float, float, float]
    rows: int
    columns: int
    geographic_bounds_wgs84: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        min_x, min_y, max_x, max_y = self.bounds_mm
        if self.rows <= 0 or self.columns <= 0:
            raise ValueError("land-cover dimensions must be positive")
        if max_x <= min_x or max_y <= min_y:
            raise ValueError("land-cover bounds must have positive area")
        if self.geographic_bounds_wgs84 is not None:
            west, south, east, north = self.geographic_bounds_wgs84
            if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
                raise ValueError("geographic bounds must be ordered WGS84 longitude/latitude")


@dataclass(frozen=True)
class LandCoverGrid:
    """Categorical cells covering rectangular bounds in print millimetres.

    Row zero is adjacent to ``min_y`` and column zero to ``min_x``.  This
    explicit convention avoids provider-specific north-up array assumptions.
    """

    classes: NDArray[np.uint8]
    bounds_mm: tuple[float, float, float, float]
    provenance: LandCoverProvenance

    def __post_init__(self) -> None:
        values = np.asarray(self.classes)
        if values.ndim != 2 or 0 in values.shape:
            raise ValueError("land-cover classes must be a non-empty 2D array")
        min_x, min_y, max_x, max_y = self.bounds_mm
        if max_x <= min_x or max_y <= min_y:
            raise ValueError("land-cover bounds must have positive area")
        valid_values = {item.value for item in LandCoverClass}
        if not set(np.unique(values)).issubset(valid_values):
            raise ValueError("land-cover grid contains an unknown class value")
        frozen = np.asarray(values, dtype=np.uint8).copy()
        frozen.setflags(write=False)
        object.__setattr__(self, "classes", frozen)

    @property
    def shape(self) -> tuple[int, int]:
        return self.classes.shape

    @property
    def cell_size_mm(self) -> tuple[float, float]:
        min_x, min_y, max_x, max_y = self.bounds_mm
        rows, columns = self.shape
        return (max_x - min_x) / columns, (max_y - min_y) / rows


@runtime_checkable
class LandCoverProvider(Protocol):
    """Interface implemented by local, cached, or network-backed providers."""

    def get_land_cover(self, request: LandCoverRequest) -> LandCoverGrid:
        """Return a grid sampled exactly to ``request``."""


@dataclass(frozen=True)
class InMemoryLandCoverProvider:
    """Deterministic provider useful for fixtures and pre-sampled local data."""

    grid: LandCoverGrid

    def get_land_cover(self, request: LandCoverRequest) -> LandCoverGrid:
        if self.grid.bounds_mm != request.bounds_mm or self.grid.shape != (
            request.rows,
            request.columns,
        ):
            raise ValueError("in-memory land-cover grid does not match request")
        return self.grid


def rasterize_geometry_mask(
    grid: LandCoverGrid,
    geometries: BaseGeometry | Iterable[BaseGeometry] | None,
) -> NDArray[np.bool_]:
    """Rasterize print-space geometry using the center of every grid cell.

    A cell is selected only when its center lies strictly inside the geometry;
    centers on an exterior or hole boundary are not selected. Geometry outside
    the grid bounds is naturally clipped. Empty input returns an all-false mask.
    Polygon holes and multipart geometry are handled by Shapely.
    """

    if geometries is None:
        return np.zeros(grid.shape, dtype=bool)
    if isinstance(geometries, BaseGeometry):
        geometry = geometries
    else:
        parts = list(geometries)
        if any(not isinstance(part, BaseGeometry) for part in parts):
            raise TypeError("geometries must contain Shapely geometry objects")
        geometry = unary_union(parts) if parts else Polygon()
    if geometry.is_empty:
        return np.zeros(grid.shape, dtype=bool)

    min_x, min_y, _, _ = grid.bounds_mm
    cell_width, cell_height = grid.cell_size_mm
    rows, columns = grid.shape
    xs = min_x + (np.arange(columns, dtype=float) + 0.5) * cell_width
    ys = min_y + (np.arange(rows, dtype=float) + 0.5) * cell_height
    sample_x, sample_y = np.meshgrid(xs, ys)
    return np.asarray(contains_xy(geometry, sample_x, sample_y), dtype=bool)


def resample_land_cover(grid: LandCoverGrid, request: LandCoverRequest) -> LandCoverGrid:
    """Nearest-neighbor sample ``grid`` onto an exact print-space request.

    Target cell centers outside the source bounds become ``UNKNOWN``. This is
    intended for categorical data and avoids introducing blended class values.
    """

    target = np.full(
        (request.rows, request.columns), LandCoverClass.UNKNOWN.value, dtype=np.uint8
    )
    source_min_x, source_min_y, source_max_x, source_max_y = grid.bounds_mm
    target_min_x, target_min_y, target_max_x, target_max_y = request.bounds_mm
    source_rows, source_columns = grid.shape
    target_xs = target_min_x + (np.arange(request.columns) + 0.5) * (
        (target_max_x - target_min_x) / request.columns
    )
    target_ys = target_min_y + (np.arange(request.rows) + 0.5) * (
        (target_max_y - target_min_y) / request.rows
    )
    source_columns_at_target = np.floor(
        (target_xs - source_min_x) / (source_max_x - source_min_x) * source_columns
    ).astype(int)
    source_rows_at_target = np.floor(
        (target_ys - source_min_y) / (source_max_y - source_min_y) * source_rows
    ).astype(int)
    valid_columns = (source_columns_at_target >= 0) & (
        source_columns_at_target < source_columns
    )
    valid_rows = (source_rows_at_target >= 0) & (source_rows_at_target < source_rows)
    if valid_columns.any() and valid_rows.any():
        target[np.ix_(valid_rows, valid_columns)] = grid.classes[
            np.ix_(source_rows_at_target[valid_rows], source_columns_at_target[valid_columns])
        ]
    provenance = LandCoverProvenance(
        provider=grid.provenance.provider,
        dataset=grid.provenance.dataset,
        edition=grid.provenance.edition,
        cached=grid.provenance.cached,
        details={**grid.provenance.details, "resampling": "nearest-cell-center"},
    )
    return LandCoverGrid(target, request.bounds_mm, provenance)


def _checked_mask(mask: NDArray[np.bool_] | None, shape: tuple[int, int], name: str):
    if mask is None:
        return None
    result = np.asarray(mask, dtype=bool)
    if result.shape != shape:
        raise ValueError(f"{name} mask shape {result.shape} does not match {shape}")
    return result


def fuse_land_cover(
    raster: LandCoverGrid,
    *,
    mapped_water: NDArray[np.bool_] | None = None,
    osm_exposed: NDArray[np.bool_] | None = None,
) -> LandCoverGrid:
    """Fuse broad raster classes with precise mapped features.

    Precedence, from highest to lowest, is mapped water, explicit OSM exposed
    ground, raster exposed ground, raster vegetation, then unknown.  Explicit
    OSM exposed ground is represented as ``BARE`` when its more specific OSM
    class has not yet been supplied by an integration layer.
    """

    shape = raster.shape
    water = _checked_mask(mapped_water, shape, "mapped_water")
    exposed = _checked_mask(osm_exposed, shape, "osm_exposed")
    source = raster.classes
    result = np.full(shape, LandCoverClass.UNKNOWN.value, dtype=np.uint8)

    vegetation = source == LandCoverClass.VEGETATION.value
    result[vegetation] = LandCoverClass.VEGETATION.value
    for category in EXPOSED_CLASSES:
        selected = source == category.value
        result[selected] = category.value
    if exposed is not None:
        result[exposed] = LandCoverClass.BARE.value
    if water is not None:
        result[water] = LandCoverClass.WATER.value

    provenance = LandCoverProvenance(
        provider="fusion",
        dataset=raster.provenance.dataset,
        edition=raster.provenance.edition,
        cached=raster.provenance.cached,
        details={
            **raster.provenance.details,
            "source_provider": raster.provenance.provider,
            "precedence": "water>osm_exposed>raster_exposed>vegetation>unknown",
        },
    )
    return LandCoverGrid(result, raster.bounds_mm, provenance)


def exposed_mask(grid: LandCoverGrid) -> NDArray[np.bool_]:
    """Return a mutable boolean mask for all printable exposed-ground classes."""

    return np.isin(grid.classes, [item.value for item in EXPOSED_CLASSES])


EVIDENCE_THRESHOLDS = {"conservative": 80, "balanced": 55, "broad": 40}


@dataclass(frozen=True)
class LandCoverEvidenceResult:
    """A fused grid plus inspectable evidence used to classify exposed ground."""

    grid: LandCoverGrid
    scores: NDArray[np.int16]
    contribution_masks: Mapping[str, NDArray[np.bool_]]
    contribution_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        scores = np.asarray(self.scores, dtype=np.int16).copy()
        if scores.shape != self.grid.shape:
            raise ValueError("evidence scores must match the output grid")
        scores.setflags(write=False)
        masks: dict[str, NDArray[np.bool_]] = {}
        for name, value in self.contribution_masks.items():
            mask = _checked_mask(value, self.grid.shape, name).copy()
            mask.setflags(write=False)
            masks[name] = mask
        object.__setattr__(self, "scores", scores)
        object.__setattr__(self, "contribution_masks", MappingProxyType(masks))
        object.__setattr__(
            self, "contribution_counts", MappingProxyType(dict(self.contribution_counts))
        )


def coastal_distance_mask(
    water: NDArray[np.bool_],
    *,
    cell_size_m: tuple[float, float],
    maximum_distance_m: float = 1000.0,
) -> NDArray[np.bool_]:
    """Return cells within a physical distance of water using an 8-way metric.

    Distances use the supplied horizontal and vertical ground sampling distance,
    rather than grid cells or print millimetres, so changing model scale does not
    change the coastal classification.
    """

    source = np.asarray(water, dtype=bool)
    if source.ndim != 2 or 0 in source.shape:
        raise ValueError("water mask must be a non-empty 2D array")
    cell_x, cell_y = cell_size_m
    if cell_x <= 0 or cell_y <= 0 or maximum_distance_m < 0:
        raise ValueError("coastal distances and cell sizes must be non-negative")
    if not source.any():
        return np.zeros(source.shape, dtype=bool)

    distances = np.full(source.shape, np.inf, dtype=float)
    queue: list[tuple[float, int, int]] = []
    for row, column in np.argwhere(source):
        distances[row, column] = 0.0
        heapq.heappush(queue, (0.0, int(row), int(column)))
    diagonal = float(np.hypot(cell_x, cell_y))
    steps = (
        (-1, -1, diagonal), (-1, 0, cell_y), (-1, 1, diagonal),
        (0, -1, cell_x), (0, 1, cell_x),
        (1, -1, diagonal), (1, 0, cell_y), (1, 1, diagonal),
    )
    while queue:
        distance, row, column = heapq.heappop(queue)
        if distance != distances[row, column] or distance > maximum_distance_m:
            continue
        for row_step, column_step, cost in steps:
            next_row, next_column = row + row_step, column + column_step
            next_distance = distance + cost
            if (
                0 <= next_row < source.shape[0]
                and 0 <= next_column < source.shape[1]
                and next_distance <= maximum_distance_m
                and next_distance < distances[next_row, next_column]
            ):
                distances[next_row, next_column] = next_distance
                heapq.heappush(queue, (next_distance, next_row, next_column))
    return distances <= maximum_distance_m


def _components_connected_to(
    candidates: NDArray[np.bool_], anchors: NDArray[np.bool_]
) -> NDArray[np.bool_]:
    adjacent = _neighbor_count(anchors) > 0
    connected = np.zeros(candidates.shape, dtype=bool)
    for component in _connected_components(candidates):
        if any(anchors[cell] or adjacent[cell] for cell in component):
            rows, columns = zip(*component)
            connected[rows, columns] = True
    return connected


def _coastal_water_components(water: NDArray[np.bool_]) -> NDArray[np.bool_]:
    """Return substantial water components connected to the raster boundary."""

    coastal = np.zeros(water.shape, dtype=bool)
    minimum_cells = max(4, int(np.ceil(water.size * 0.01)))
    last_row, last_column = water.shape[0] - 1, water.shape[1] - 1
    for component in _connected_components(water):
        if len(component) < minimum_cells:
            continue
        if not any(
            row in {0, last_row} or column in {0, last_column}
            for row, column in component
        ):
            continue
        rows, columns = zip(*component)
        coastal[rows, columns] = True
    return coastal


def fuse_coastal_evidence(
    worldcover: LandCoverGrid,
    impact: LandCoverGrid,
    *,
    mapped_water: NDArray[np.bool_] | None = None,
    osm_exposed: NDArray[np.bool_] | None = None,
    cliff_or_outcrop: NDArray[np.bool_] | None = None,
    strong_vegetation: NDArray[np.bool_] | None = None,
    cell_size_m: tuple[float, float],
    coastal_distance_m: float = 1000.0,
    sensitivity: str = "balanced",
) -> LandCoverEvidenceResult:
    """Fuse two normalized rasters and mapped evidence into exposed ground."""

    if worldcover.shape != impact.shape or worldcover.bounds_mm != impact.bounds_mm:
        raise ValueError("evidence grids must share bounds and dimensions")
    try:
        threshold = EVIDENCE_THRESHOLDS[sensitivity]
    except KeyError as exc:
        raise ValueError(f"Unsupported ground-cover sensitivity: {sensitivity}") from exc
    shape = worldcover.shape
    water = _checked_mask(mapped_water, shape, "mapped_water")
    water = np.zeros(shape, dtype=bool) if water is None else water.copy()
    water |= (worldcover.classes == LandCoverClass.WATER) | (
        impact.classes == LandCoverClass.WATER
    )
    explicit = _checked_mask(osm_exposed, shape, "osm_exposed")
    explicit = np.zeros(shape, dtype=bool) if explicit is None else explicit.copy()
    cliff = _checked_mask(cliff_or_outcrop, shape, "cliff_or_outcrop")
    cliff = np.zeros(shape, dtype=bool) if cliff is None else cliff.copy()
    wc_bare = np.isin(worldcover.classes, [item.value for item in EXPOSED_CLASSES])
    impact_bare = np.isin(impact.classes, [item.value for item in EXPOSED_CLASSES])
    built = (worldcover.classes == LandCoverClass.BUILT) | (
        impact.classes == LandCoverClass.BUILT
    )
    if strong_vegetation is None:
        vegetation = (worldcover.classes == LandCoverClass.VEGETATION) & (
            impact.classes == LandCoverClass.VEGETATION
        )
    else:
        vegetation = _checked_mask(strong_vegetation, shape, "strong_vegetation").copy()
    coastal_water = _coastal_water_components(water)
    coastal = coastal_distance_mask(
        coastal_water,
        cell_size_m=cell_size_m,
        maximum_distance_m=coastal_distance_m,
    )
    candidates = wc_bare | impact_bare | explicit
    connected = _components_connected_to(candidates, water | explicit)

    scores = np.zeros(shape, dtype=np.int16)
    scores[wc_bare] += 45
    scores[impact_bare] += 45
    scores[coastal] += 15
    scores[connected] += 15
    scores[cliff] += 15
    scores[vegetation] -= 50
    exposed = (scores >= threshold) | explicit
    exposed[built & ~explicit] = False
    exposed[water] = False

    classes = np.full(shape, LandCoverClass.UNKNOWN, dtype=np.uint8)
    classes[vegetation] = LandCoverClass.VEGETATION
    classes[built] = LandCoverClass.BUILT
    classes[exposed] = LandCoverClass.BARE
    classes[water] = LandCoverClass.WATER
    masks = {
        "worldcover_bare": wc_bare,
        "impact_bare": impact_bare,
        "coastal": coastal,
        "coastal_water": coastal_water,
        "connected": connected,
        "cliff_or_outcrop": cliff,
        "strong_vegetation": vegetation,
        "built_rejected": built & ~explicit,
        "osm_exposed": explicit,
        "final_exposed": exposed,
    }
    provenance = LandCoverProvenance(
        provider="evidence-fusion",
        dataset=f"{worldcover.provenance.dataset}+{impact.provenance.dataset}+OSM",
        edition="+".join(
            value for value in (worldcover.provenance.edition, impact.provenance.edition) if value
        ) or None,
        cached=worldcover.provenance.cached and impact.provenance.cached,
        details={
            "worldcover_provider": worldcover.provenance.provider,
            "impact_provider": impact.provenance.provider,
            "sensitivity": sensitivity,
            "threshold": str(threshold),
            "coastal_distance_m": str(float(coastal_distance_m)),
        },
    )
    grid = LandCoverGrid(classes, worldcover.bounds_mm, provenance)
    return LandCoverEvidenceResult(
        grid, scores, masks, {name: int(mask.sum()) for name, mask in masks.items()}
    )


def _neighbor_count(mask: NDArray[np.bool_]) -> NDArray[np.uint8]:
    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    counts = np.zeros(mask.shape, dtype=np.uint8)
    for row_offset in range(3):
        for column_offset in range(3):
            if row_offset == 1 and column_offset == 1:
                continue
            counts += padded[
                row_offset : row_offset + mask.shape[0],
                column_offset : column_offset + mask.shape[1],
            ]
    return counts


def _connected_components(mask: NDArray[np.bool_]):
    """Yield 8-connected component coordinates without optional dependencies."""

    rows, columns = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    for seed_row in range(rows):
        for seed_column in range(columns):
            if not mask[seed_row, seed_column] or visited[seed_row, seed_column]:
                continue
            stack = [(seed_row, seed_column)]
            visited[seed_row, seed_column] = True
            component = []
            while stack:
                row, column = stack.pop()
                component.append((row, column))
                for adjacent_row in range(max(0, row - 1), min(rows, row + 2)):
                    for adjacent_column in range(
                        max(0, column - 1), min(columns, column + 2)
                    ):
                        if (
                            mask[adjacent_row, adjacent_column]
                            and not visited[adjacent_row, adjacent_column]
                        ):
                            visited[adjacent_row, adjacent_column] = True
                            stack.append((adjacent_row, adjacent_column))
            yield component


def cleanup_exposed_mask(
    grid: LandCoverGrid,
    *,
    excluded_water: NDArray[np.bool_] | None = None,
    sensitivity: str | GroundCoverCleanupPreset = "balanced",
) -> NDArray[np.bool_]:
    """Return a print-aware, cleaned exposed-ground mask.

    Tiny isolated regions are removed unless their print-space span shows that
    they are a useful long, narrow feature. Tiny enclosed gaps are filled, and
    an optional single-pixel majority pass softens raster stair steps without
    eroding narrow centerlines. Cells excluded as water remain false throughout.
    """

    preset = resolve_ground_cover_cleanup_preset(sensitivity)
    water = _checked_mask(excluded_water, grid.shape, "excluded_water")
    if water is None:
        water = grid.classes == LandCoverClass.WATER.value
    else:
        water = water | (grid.classes == LandCoverClass.WATER.value)
    result = exposed_mask(grid)
    result[water] = False

    for _ in range(preset.smoothing_passes):
        neighbors = _neighbor_count(result)
        # Fill only strongly surrounded cells and remove only isolated pixels.
        # One-cell-wide features, including their endpoints, remain intact.
        result = (result & (neighbors >= 1)) | (~result & (neighbors >= 6))
        result[water] = False

    cell_width, cell_height = grid.cell_size_mm
    cell_area = cell_width * cell_height
    for component in list(_connected_components(result)):
        component_rows = [cell[0] for cell in component]
        component_columns = [cell[1] for cell in component]
        area = len(component) * cell_area
        span = max(
            (max(component_rows) - min(component_rows) + 1) * cell_height,
            (max(component_columns) - min(component_columns) + 1) * cell_width,
        )
        if (
            area < preset.minimum_island_area_mm2
            and span < preset.preserve_narrow_span_mm
        ):
            rows, columns = zip(*component)
            result[rows, columns] = False

    fillable = ~result & ~water
    row_count, column_count = grid.shape
    for component in _connected_components(fillable):
        touches_boundary = any(
            row in {0, row_count - 1} or column in {0, column_count - 1}
            for row, column in component
        )
        if (
            not touches_boundary
            and len(component) * cell_area <= preset.maximum_hole_area_mm2
        ):
            rows, columns = zip(*component)
            result[rows, columns] = True

    result[water] = False
    return result


def polygonize_exposed_mask(
    grid: LandCoverGrid,
    *,
    minimum_area_mm2: float = 0.0,
    minimum_width_mm: float = 0.0,
) -> BaseGeometry:
    """Convert exposed cells to filtered print-space polygons.

    Adjacent cells are dissolved before filtering. ``minimum_width_mm`` uses a
    morphological opening (negative then positive buffer), removing features
    that cannot carry the requested printed line width while retaining the
    original cell-aligned boundary of surviving components.
    """

    if minimum_area_mm2 < 0 or minimum_width_mm < 0:
        raise ValueError("polygon filters must be non-negative")
    mask = exposed_mask(grid)
    if not mask.any():
        return Polygon()
    min_x, min_y, _, _ = grid.bounds_mm
    cell_width, cell_height = grid.cell_size_mm
    # Polygonize contiguous horizontal runs instead of allocating one Shapely
    # box per cell. Large categorical grids often contain thousands of adjacent
    # cells; run-length boxes preserve the exact cell boundary while making the
    # subsequent union substantially cheaper.
    runs = []
    for row in range(mask.shape[0]):
        selected = np.flatnonzero(mask[row])
        if not selected.size:
            continue
        breaks = np.flatnonzero(np.diff(selected) > 1)
        starts = np.concatenate(([0], breaks + 1))
        stops = np.concatenate((breaks, [selected.size - 1]))
        y0 = min_y + row * cell_height
        for start_index, stop_index in zip(starts, stops, strict=True):
            first_column = int(selected[start_index])
            last_column = int(selected[stop_index]) + 1
            runs.append(
                box(
                    min_x + first_column * cell_width,
                    y0,
                    min_x + last_column * cell_width,
                    y0 + cell_height,
                )
            )
    dissolved = unary_union(runs)
    candidates = list(dissolved.geoms) if isinstance(dissolved, MultiPolygon) else [dissolved]
    kept = []
    radius = minimum_width_mm / 2.0
    for candidate in candidates:
        if candidate.area < minimum_area_mm2:
            continue
        if radius and candidate.buffer(-radius).is_empty:
            continue
        kept.append(candidate)
    return unary_union(kept) if kept else Polygon()
