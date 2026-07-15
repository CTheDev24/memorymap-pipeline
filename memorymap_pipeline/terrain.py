from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
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
        normalized = np.clip((values - low) / (high - low), 0.0, 1.0)
    return TerrainSurface(
        heights_mm=normalized * analysis.target_relief_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        analysis=analysis,
    )


def build_terrain_mesh(surface: TerrainSurface, base_thickness_mm: float) -> Trimesh:
    """Create a watertight terrain solid above a flat structural bottom."""
    if base_thickness_mm <= 0:
        raise ValueError("Terrain base thickness must be positive")
    rows, columns = surface.heights_mm.shape
    xs = np.linspace(0.0, surface.width_mm, columns)
    ys = np.linspace(surface.height_mm, 0.0, rows)
    top = np.array(
        [(x, y, surface.heights_mm[row, column]) for row, y in enumerate(ys) for column, x in enumerate(xs)],
        dtype=float,
    )
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
    return Trimesh(vertices=vertices, faces=np.asarray(faces, dtype=int), process=True)


def drape_mesh(mesh: Trimesh, surface: TerrainSurface | object) -> Trimesh:
    """Return a copy translated vertex-by-vertex onto the local terrain surface."""
    result = mesh.copy()
    sampler = getattr(surface, "sample", surface)
    result.vertices[:, 2] += sampler(result.vertices[:, 0], result.vertices[:, 1])
    return result
