from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

import numpy as np
from shapely import contains_xy
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from trimesh import Trimesh


@dataclass(frozen=True)
class TerrainAnalysis:
    minimum_m: float
    maximum_m: float
    robust_range_m: float
    flatness_rating: int
    target_relief_mm: float
    vertical_scale_mm_per_m: float


@dataclass(frozen=True)
class TerrainGridSpec:
    """Resolved DEM sampling dimensions and print-space diagnostics."""

    rows: int
    columns: int
    mode: str
    target_cell_size_mm: float | None
    cell_width_mm: float
    cell_height_mm: float

    @property
    def shape(self) -> tuple[int, int]:
        return self.rows, self.columns

    @property
    def sample_count(self) -> int:
        return self.rows * self.columns

    @property
    def estimated_terrain_faces(self) -> int:
        # The terrain solid has top and bottom triangles plus two triangles per
        # perimeter segment.
        cells = (self.rows - 1) * (self.columns - 1)
        perimeter = 2 * (self.rows - 1) + 2 * (self.columns - 1)
        return 4 * cells + 2 * perimeter


def terrain_grid_for_print(
    width_mm: float,
    height_mm: float,
    *,
    override: int | tuple[int, int] | list[int] | None = None,
    target_cell_size_mm: float = 0.55,
    maximum_samples: int = 180_000,
    maximum_dimension: int = 512,
) -> TerrainGridSpec:
    """Resolve a rectangular terrain grid from the finished print dimensions.

    Explicit legacy integer overrides remain square. A two-value override is
    interpreted as ``(rows, columns)``. Adaptive dimensions preserve the print
    aspect ratio and are capped before mesh construction so the terrain solid
    cannot unexpectedly grow to several million triangles.
    """
    if width_mm <= 0 or height_mm <= 0:
        raise ValueError("Terrain print dimensions must be positive")

    if override is not None:
        if isinstance(override, bool):
            raise ValueError("Terrain grid override must contain integer dimensions")
        if isinstance(override, (int, np.integer)):
            rows = columns = int(override)
        elif isinstance(override, (tuple, list)) and len(override) == 2:
            rows, columns = override
            if (
                isinstance(rows, bool)
                or isinstance(columns, bool)
                or not isinstance(rows, (int, np.integer))
                or not isinstance(columns, (int, np.integer))
            ):
                raise ValueError("Terrain grid override must contain integer dimensions")
            rows, columns = int(rows), int(columns)
        else:
            raise ValueError("Terrain grid override must be an integer or (rows, columns)")
        if min(rows, columns) < 2:
            raise ValueError("Terrain grid dimensions must be at least two")
        return TerrainGridSpec(
            rows=rows,
            columns=columns,
            mode="override",
            target_cell_size_mm=None,
            cell_width_mm=width_mm / (columns - 1),
            cell_height_mm=height_mm / (rows - 1),
        )

    if target_cell_size_mm <= 0:
        raise ValueError("Terrain target cell size must be positive")
    if maximum_samples < 4 or maximum_dimension < 2:
        raise ValueError("Terrain adaptive grid limits are invalid")

    desired_rows = max(2, int(np.ceil(height_mm / target_cell_size_mm)) + 1)
    desired_columns = max(2, int(np.ceil(width_mm / target_cell_size_mm)) + 1)
    scale = min(
        1.0,
        (maximum_dimension - 1) / float(desired_rows - 1),
        (maximum_dimension - 1) / float(desired_columns - 1),
        np.sqrt(maximum_samples / float(desired_rows * desired_columns)),
    )
    rows = max(2, int(np.floor((desired_rows - 1) * scale)) + 1)
    columns = max(2, int(np.floor((desired_columns - 1) * scale)) + 1)
    while rows * columns > maximum_samples:
        if rows / height_mm >= columns / width_mm:
            rows -= 1
        else:
            columns -= 1

    return TerrainGridSpec(
        rows=rows,
        columns=columns,
        mode="adaptive",
        target_cell_size_mm=float(target_cell_size_mm),
        cell_width_mm=width_mm / (columns - 1),
        cell_height_mm=height_mm / (rows - 1),
    )


@dataclass(frozen=True)
class ElevationGrid:
    """Bare-earth elevations for a geographic bounding box.

    Rows run north to south and columns run west to east. Missing values must be NaN.
    """

    elevations_m: np.ndarray
    south: float
    north: float
    west: float
    east: float
    source: str

    def __post_init__(self) -> None:
        values = np.asarray(self.elevations_m, dtype=float)
        if values.ndim != 2 or min(values.shape) < 2:
            raise ValueError("Elevation grid must contain at least two rows and columns")
        if not np.isfinite(values).any():
            raise ValueError("Elevation grid contains no finite samples")
        if self.south >= self.north or self.west >= self.east:
            raise ValueError("Elevation grid geographic bounds are invalid")
        object.__setattr__(self, "elevations_m", values)


@dataclass(frozen=True)
class TerrainSurface:
    """A print-space height field whose top values are millimeters above Z=0."""

    heights_mm: np.ndarray
    width_mm: float
    height_mm: float
    analysis: TerrainAnalysis

    def __post_init__(self) -> None:
        values = np.asarray(self.heights_mm, dtype=float)
        if values.ndim != 2 or min(values.shape) < 2:
            raise ValueError("Terrain surface must contain at least two rows and columns")
        if not np.isfinite(values).all():
            raise ValueError("Terrain surface contains invalid heights")
        if self.width_mm <= 0 or self.height_mm <= 0:
            raise ValueError("Terrain surface dimensions must be positive")
        object.__setattr__(self, "heights_mm", values)

    def sample(self, x_mm: np.ndarray | float, y_mm: np.ndarray | float) -> np.ndarray:
        """Bilinearly sample the surface; print-space Y increases south to north."""
        x = np.clip(np.asarray(x_mm, dtype=float), 0.0, self.width_mm)
        y = np.clip(np.asarray(y_mm, dtype=float), 0.0, self.height_mm)
        rows, columns = self.heights_mm.shape
        column = x / self.width_mm * (columns - 1)
        row = (1.0 - y / self.height_mm) * (rows - 1)
        c0 = np.floor(column).astype(int)
        r0 = np.floor(row).astype(int)
        c1 = np.minimum(c0 + 1, columns - 1)
        r1 = np.minimum(r0 + 1, rows - 1)
        tx = column - c0
        ty = row - r0
        north = self.heights_mm[r0, c0] * (1.0 - tx) + self.heights_mm[r0, c1] * tx
        south = self.heights_mm[r1, c0] * (1.0 - tx) + self.heights_mm[r1, c1] * tx
        return north * (1.0 - ty) + south * ty


class ElevationProvider(Protocol):
    name: str

    def fetch(
        self,
        bounds: tuple[float, float, float, float],
        grid_size: tuple[int, int],
        cache_dir: Path,
    ) -> ElevationGrid: ...


class RoadProfileCorridor(Protocol):
    centerline: LineString
    region: BaseGeometry
    width_mm: float
    classification: str


def analyze_terrain(
    elevations_m: np.ndarray,
    horizontal_span_m: float,
    maximum_relief_mm: float = 3.0,
    minimum_relief_mm: float = 1.5,
) -> TerrainAnalysis:
    """Rate terrain from 0 (rugged) to 5 (very flat) and choose print relief.

    The rating uses the robust 5th-to-95th percentile elevation range relative to the
    geographic frame diagonal. Flat frames receive the full configured 3 mm relief;
    naturally rugged frames taper toward 1.5 mm so terrain does not dominate overlays.
    """
    values = np.asarray(elevations_m, dtype=float)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("Cannot analyze terrain without finite elevation samples")
    if horizontal_span_m <= 0 or minimum_relief_mm < 0 or maximum_relief_mm <= 0:
        raise ValueError("Terrain analysis dimensions must be positive")
    if minimum_relief_mm > maximum_relief_mm:
        raise ValueError("Minimum relief cannot exceed maximum relief")
    low, high = np.percentile(finite, [5.0, 95.0])
    robust_range = max(0.0, float(high - low))
    relief_ratio = robust_range / horizontal_span_m
    flatness = float(np.clip(1.0 - relief_ratio / 0.08, 0.0, 1.0))
    rating = int(np.clip(np.rint(flatness * 5.0), 0, 5))
    target = minimum_relief_mm + flatness * (maximum_relief_mm - minimum_relief_mm)
    scale = target / robust_range if robust_range > 1e-9 else 0.0
    return TerrainAnalysis(
        minimum_m=float(np.min(finite)),
        maximum_m=float(np.max(finite)),
        robust_range_m=robust_range,
        flatness_rating=rating,
        target_relief_mm=float(target),
        vertical_scale_mm_per_m=float(scale),
    )


def terrain_surface_from_grid(
    grid: ElevationGrid,
    width_mm: float,
    height_mm: float,
    horizontal_span_m: float,
    maximum_relief_mm: float = 3.0,
    minimum_relief_mm: float = 1.5,
) -> TerrainSurface:
    analysis = analyze_terrain(
        grid.elevations_m,
        horizontal_span_m,
        maximum_relief_mm,
        minimum_relief_mm,
    )
    values = grid.elevations_m.copy()
    if not np.isfinite(values).all():
        values[~np.isfinite(values)] = float(np.nanmedian(values))
    low, high = np.percentile(values, [5.0, 95.0])
    if high - low <= 1e-9:
        normalized = np.zeros_like(values)
    else:
        # Preserve the robust percentile range without turning the lower and
        # upper five percent into flat shelves.  The tails receive a compact,
        # monotonic five percent of the printed relief instead of being clipped.
        robust_range = high - low
        central = 0.05 + 0.90 * (values - low) / robust_range
        normalized = central.copy()
        below = values < low
        above = values > high
        normalized[below] = 0.05 * np.exp(
            (values[below] - low) / robust_range
        )
        normalized[above] = 1.0 - 0.05 * np.exp(
            -(values[above] - high) / robust_range
        )
        minimum = float(np.min(normalized))
        maximum = float(np.max(normalized))
        normalized = (normalized - minimum) / (maximum - minimum)
    return TerrainSurface(
        heights_mm=normalized * analysis.target_relief_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        analysis=analysis,
    )


def terrain_mesh_axes(
    surface: TerrainSurface,
    flat_margin_mm: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return terrain axes with exact inner trim boundaries when requested."""
    if flat_margin_mm < 0:
        raise ValueError("Terrain trim margin cannot be negative")
    if flat_margin_mm * 2.0 >= min(surface.width_mm, surface.height_mm):
        raise ValueError("Terrain trim margin is too large")
    rows, columns = surface.heights_mm.shape
    xs = np.linspace(0.0, surface.width_mm, columns)
    ys = np.linspace(surface.height_mm, 0.0, rows)
    if flat_margin_mm > 0.0:
        xs = np.unique(
            np.append(
                xs,
                (flat_margin_mm, surface.width_mm - flat_margin_mm),
            )
        )
        ys = np.unique(
            np.append(
                ys,
                (flat_margin_mm, surface.height_mm - flat_margin_mm),
            )
        )[::-1]
    return xs, ys


def terrain_mesh_heights(
    surface: TerrainSurface,
    x_mm: np.ndarray,
    y_mm: np.ndarray,
    flat_margin_mm: float = 0.0,
) -> np.ndarray:
    """Sample terrain while keeping the outer trim at one constant elevation."""
    heights = np.asarray(surface.sample(x_mm, y_mm), dtype=float)
    if flat_margin_mm <= 0.0:
        return heights
    flat_height = float(np.min(surface.heights_mm))
    trim = (
        (x_mm <= flat_margin_mm + 1e-9)
        | (x_mm >= surface.width_mm - flat_margin_mm - 1e-9)
        | (y_mm <= flat_margin_mm + 1e-9)
        | (y_mm >= surface.height_mm - flat_margin_mm - 1e-9)
    )
    return np.where(trim, flat_height, heights)


def build_terrain_mesh(
    surface: TerrainSurface,
    base_thickness_mm: float,
    flat_margin_mm: float = 0.0,
) -> Trimesh:
    """Create a watertight terrain solid above a flat structural bottom."""
    if base_thickness_mm <= 0:
        raise ValueError("Terrain base thickness must be positive")
    xs, ys = terrain_mesh_axes(surface, flat_margin_mm)
    x_grid, y_grid = np.meshgrid(xs, ys)
    top = np.column_stack(
        (
            x_grid.reshape(-1),
            y_grid.reshape(-1),
            terrain_mesh_heights(
                surface,
                x_grid,
                y_grid,
                flat_margin_mm,
            ).reshape(-1),
        )
    )
    rows, columns = len(ys), len(xs)
    bottom = top.copy()
    bottom[:, 2] = -base_thickness_mm
    vertices = np.vstack((top, bottom))
    layer_size = rows * columns
    faces: list[tuple[int, int, int]] = []

    for row in range(rows - 1):
        for column in range(columns - 1):
            a = row * columns + column
            b = a + 1
            c = a + columns
            d = c + 1
            faces.extend(((a, c, b), (b, c, d)))
            faces.extend(
                (
                    (a + layer_size, b + layer_size, c + layer_size),
                    (b + layer_size, d + layer_size, c + layer_size),
                )
            )

    perimeter: list[int] = []
    perimeter.extend(range(columns))
    perimeter.extend(row * columns + columns - 1 for row in range(1, rows))
    perimeter.extend((rows - 1) * columns + column for column in range(columns - 2, -1, -1))
    perimeter.extend(row * columns for row in range(rows - 2, 0, -1))
    for start, end in zip(perimeter, perimeter[1:] + perimeter[:1]):
        faces.extend(
            (
                (start, end + layer_size, end),
                (start, start + layer_size, end + layer_size),
            )
        )
    mesh = Trimesh(vertices=vertices, faces=np.asarray(faces, dtype=int), process=True)
    mesh.fix_normals(multibody=True)
    return mesh


def _smoothed_samples(
    sampler: object,
    x_mm: np.ndarray,
    y_mm: np.ndarray,
    radius_mm: float,
) -> np.ndarray:
    """Return a compact Gaussian-like average around each print-space point."""
    sample = getattr(sampler, "sample", sampler)
    offsets = (-radius_mm, 0.0, radius_mm)
    weights = (1.0, 2.0, 1.0)
    total = np.zeros_like(x_mm, dtype=float)
    total_weight = 0.0
    for x_offset, x_weight in zip(offsets, weights):
        for y_offset, y_weight in zip(offsets, weights):
            weight = x_weight * y_weight
            total += weight * sample(x_mm + x_offset, y_mm + y_offset)
            total_weight += weight
    return total / total_weight


def drape_mesh(
    mesh: Trimesh,
    surface: TerrainSurface | object,
    *,
    smooth_top_region: BaseGeometry | None = None,
    smoothing_radius_mm: float = 0.0,
    minimum_visible_height_mm: float = 0.0,
) -> Trimesh:
    """Return a copy translated vertex-by-vertex onto the local terrain surface.

    When ``smooth_top_region`` is supplied, only top vertices inside that region use a
    low-pass terrain sample. Bottom vertices continue to follow the unsmoothed surface,
    keeping the feature embedded and printable instead of turning it into a floating deck.
    """
    result = mesh.copy()
    sampler = getattr(surface, "sample", surface)
    x = result.vertices[:, 0]
    y = result.vertices[:, 1]
    original_z = result.vertices[:, 2].copy()
    terrain_offsets = np.asarray(sampler(x, y), dtype=float)

    if smooth_top_region is not None and smoothing_radius_mm > 0.0:
        region = smooth_top_region.buffer(1e-7)
        top_z = float(np.max(original_z))
        top_vertices = np.isclose(original_z, top_z, atol=1e-7)
        in_region = contains_xy(region, x, y)
        smooth_vertices = top_vertices & in_region
        if np.any(smooth_vertices):
            smoothed = _smoothed_samples(
                sampler,
                x[smooth_vertices],
                y[smooth_vertices],
                smoothing_radius_mm,
            )
            # Never let smoothing bury the top completely beneath a local terrain peak.
            minimum_offset = (
                terrain_offsets[smooth_vertices]
                + minimum_visible_height_mm
                - original_z[smooth_vertices]
            )
            terrain_offsets[smooth_vertices] = np.maximum(smoothed, minimum_offset)

    result.vertices[:, 2] = original_z + terrain_offsets
    return result


def _route_cross_section_support(
    centerline: LineString,
    stations: np.ndarray,
    sampler: object,
    route_width_mm: float,
) -> np.ndarray:
    """Sample the highest terrain point across each route-normal cross-section."""
    length = float(centerline.length)
    tangent_delta = min(1.0, max(0.05, length / 500.0))
    centers = np.empty((len(stations), 2), dtype=float)
    normals = np.empty((len(stations), 2), dtype=float)
    for index, station in enumerate(stations):
        distance = float(np.clip(station, 0.0, length))
        center = centerline.interpolate(distance)
        before = centerline.interpolate(max(0.0, distance - tangent_delta))
        after = centerline.interpolate(min(length, distance + tangent_delta))
        tangent = np.array([after.x - before.x, after.y - before.y], dtype=float)
        magnitude = float(np.linalg.norm(tangent))
        if magnitude <= 1e-9:
            tangent = np.array([1.0, 0.0])
        else:
            tangent /= magnitude
        centers[index] = (center.x, center.y)
        normals[index] = (-tangent[1], tangent[0])

    offsets = np.linspace(-route_width_mm / 2.0, route_width_mm / 2.0, 5)
    xs = centers[:, 0, None] + normals[:, 0, None] * offsets
    ys = centers[:, 1, None] + normals[:, 1, None] * offsets
    sample = getattr(sampler, "sample", sampler)
    return np.max(np.asarray(sample(xs, ys), dtype=float), axis=1)


def drape_route_mesh(
    mesh: Trimesh,
    centerline: LineString,
    surface: TerrainSurface | object,
    *,
    route_width_mm: float,
    visible_height_mm: float,
    smoothing_distance_mm: float = 1.5,
) -> Trimesh:
    """Drape a constant-width route with one terrain height per cross-section.

    The underside follows terrain vertex-by-vertex for continuous support. Top vertices
    project to the route centerline, sample the full route width, and smooth only along
    the direction of travel. This prevents terrain triangles from twisting the orange
    surface from one edge of the route to the other.
    """
    if centerline.is_empty or centerline.length <= 0.0:
        raise ValueError("Route centerline must have positive length")
    if route_width_mm <= 0.0 or visible_height_mm <= 0.0:
        raise ValueError("Route width and visible height must be positive")
    if smoothing_distance_mm < 0.0:
        raise ValueError("Route smoothing distance cannot be negative")

    result = mesh.copy()
    sample = getattr(surface, "sample", surface)
    x = result.vertices[:, 0]
    y = result.vertices[:, 1]
    original_z = result.vertices[:, 2].copy()
    raw_support = np.asarray(sample(x, y), dtype=float)
    result.vertices[:, 2] = original_z + raw_support

    top_z = float(np.max(original_z))
    top_indices = np.flatnonzero(np.isclose(original_z, top_z, atol=1e-7))
    if not len(top_indices):
        return result
    stations = np.array(
        [centerline.project(Point(x[index], y[index])) for index in top_indices],
        dtype=float,
    )
    local_support = _route_cross_section_support(
        centerline, stations, sample, route_width_mm
    )
    if smoothing_distance_mm > 0.0:
        neighboring_support = [
            _route_cross_section_support(
                centerline,
                np.clip(stations + offset, 0.0, centerline.length),
                sample,
                route_width_mm,
            )
            for offset in (-smoothing_distance_mm, 0.0, smoothing_distance_mm)
        ]
        profile_support = (
            neighboring_support[0]
            + 2.0 * neighboring_support[1]
            + neighboring_support[2]
        ) / 4.0
    else:
        profile_support = local_support

    # Smoothing may raise neighboring valleys but never reduces the requested visible
    # height over the highest terrain sample in the current cross-section.
    top_heights = np.maximum(profile_support, local_support) + visible_height_mm
    result.vertices[top_indices, 2] = top_heights
    return result


def drape_road_mesh(
    mesh: Trimesh,
    surface: TerrainSurface | object,
    corridors: tuple[RoadProfileCorridor, ...],
    *,
    visible_height_mm: float,
    minimum_visible_height_mm: float,
    smoothing_distances_mm: Mapping[str, float],
) -> Trimesh:
    """Drape roads while directionally grading selected major-road corridors.

    Minor roads retain vertex-level terrain relief. A selected freeway or highway uses
    one terrain support value across its width, smoothed only along its centerline. Its
    underside still follows raw terrain so the resulting solid remains supported. At
    junctions the highest applicable profile wins, avoiding surface seams.
    """
    if visible_height_mm <= 0.0:
        raise ValueError("Road visible height must be positive")
    if not 0.0 <= minimum_visible_height_mm <= visible_height_mm:
        raise ValueError(
            "Minimum road height must be between zero and the visible road height"
        )

    result = mesh.copy()
    sample = getattr(surface, "sample", surface)
    x = result.vertices[:, 0]
    y = result.vertices[:, 1]
    original_z = result.vertices[:, 2].copy()
    raw_support = np.asarray(sample(x, y), dtype=float)
    result.vertices[:, 2] = original_z + raw_support

    top_z = float(np.max(original_z))
    top_indices = np.flatnonzero(np.isclose(original_z, top_z, atol=1e-7))
    if not len(top_indices) or not corridors:
        return result

    profiled_heights = np.full(len(top_indices), -np.inf, dtype=float)
    top_x = x[top_indices]
    top_y = y[top_indices]
    for corridor in corridors:
        distance = float(smoothing_distances_mm.get(corridor.classification, 0.0))
        if distance <= 0.0 or corridor.centerline.is_empty:
            continue
        selected = contains_xy(corridor.region.buffer(1e-7), top_x, top_y)
        selected_positions = np.flatnonzero(selected)
        if not len(selected_positions):
            continue
        stations = np.array(
            [
                corridor.centerline.project(Point(top_x[index], top_y[index]))
                for index in selected_positions
            ],
            dtype=float,
        )
        local_support = _route_cross_section_support(
            corridor.centerline,
            stations,
            sample,
            corridor.width_mm,
        )
        neighboring_support = [
            _route_cross_section_support(
                corridor.centerline,
                np.clip(stations + offset, 0.0, corridor.centerline.length),
                sample,
                corridor.width_mm,
            )
            for offset in (-distance, 0.0, distance)
        ]
        smooth_support = (
            neighboring_support[0]
            + 2.0 * neighboring_support[1]
            + neighboring_support[2]
        ) / 4.0
        desired = np.maximum(
            smooth_support + visible_height_mm,
            local_support + minimum_visible_height_mm,
        )
        profiled_heights[selected_positions] = np.maximum(
            profiled_heights[selected_positions], desired
        )

    profiled = np.isfinite(profiled_heights)
    result.vertices[top_indices[profiled], 2] = profiled_heights[profiled]
    return result
