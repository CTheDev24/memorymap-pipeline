from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np

from .projection import project_lonlat_array, project_points


@dataclass(frozen=True)
class MapFrame:
    """A geographic print frame shared by every generated map layer.

    ``rotation_degrees`` is counter-clockwise from east in geographic space.
    Coordinates returned by the transform are millimetres from the lower-left
    corner of the physical print.
    """

    center_lat: float
    center_lon: float
    coverage_width_m: float
    coverage_height_m: float
    print_width_mm: float
    print_height_mm: float
    margin_mm: float = 0.0
    rotation_degrees: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.center_lat,
            self.center_lon,
            self.coverage_width_m,
            self.coverage_height_m,
            self.print_width_mm,
            self.print_height_mm,
            self.margin_mm,
            self.rotation_degrees,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Map frame values must be finite")
        if not -90.0 <= self.center_lat <= 90.0:
            raise ValueError("center_lat must be between -90 and 90")
        if not -180.0 <= self.center_lon <= 180.0:
            raise ValueError("center_lon must be between -180 and 180")
        if self.coverage_width_m <= 0 or self.coverage_height_m <= 0:
            raise ValueError("Coverage dimensions must be positive")
        if self.print_width_mm <= 0 or self.print_height_mm <= 0:
            raise ValueError("Print dimensions must be positive")
        if self.printable_width_mm <= 0 or self.printable_height_mm <= 0:
            raise ValueError("Margin is too large for the print dimensions")

    @property
    def printable_width_mm(self) -> float:
        return self.print_width_mm - 2.0 * self.margin_mm

    @property
    def printable_height_mm(self) -> float:
        return self.print_height_mm - 2.0 * self.margin_mm

    @classmethod
    def fit_route(
        cls,
        points: Sequence[object],
        print_width_mm: float,
        print_height_mm: float,
        margin_mm: float = 0.0,
        rotation_degrees: float = 0.0,
    ) -> "MapFrame":
        """Return a route-centred frame with the current fit-and-centre behavior."""
        if not points:
            raise ValueError("Cannot fit a map frame to an empty route")
        latitudes = np.asarray([point.latitude for point in points], dtype=float)
        longitudes = np.asarray([point.longitude for point in points], dtype=float)
        center_lat = float((latitudes.min() + latitudes.max()) / 2.0)
        center_lon = float((longitudes.min() + longitudes.max()) / 2.0)
        projected = project_points(points, center_lat, center_lon)
        rotated = _rotate(projected, -rotation_degrees)
        spans = np.ptp(rotated, axis=0)

        printable_width = print_width_mm - 2.0 * margin_mm
        printable_height = print_height_mm - 2.0 * margin_mm
        if printable_width <= 0 or printable_height <= 0:
            raise ValueError("Margin is too large for the print dimensions")

        # Expand the geographic bounds to the printable area's aspect ratio.
        span_x, span_y = float(spans[0]), float(spans[1])
        minimum_coverage = 1e-6
        scale = max(
            span_x / printable_width if span_x else 0.0,
            span_y / printable_height if span_y else 0.0,
            minimum_coverage,
        )
        return cls(
            center_lat=center_lat,
            center_lon=center_lon,
            coverage_width_m=printable_width * scale,
            coverage_height_m=printable_height * scale,
            print_width_mm=print_width_mm,
            print_height_mm=print_height_mm,
            margin_mm=margin_mm,
            rotation_degrees=rotation_degrees,
        )

    def transform_projected(self, projected: np.ndarray) -> np.ndarray:
        """Transform metres projected around this frame's center to print mm."""
        coordinates = np.asarray(projected, dtype=float)
        if coordinates.ndim != 2 or coordinates.shape[1] != 2:
            raise ValueError("Projected coordinates must be an Nx2 array")
        rotated = _rotate(coordinates, -self.rotation_degrees)
        x = self.print_width_mm / 2.0 + rotated[:, 0] * (
            self.printable_width_mm / self.coverage_width_m
        )
        y = self.print_height_mm / 2.0 + rotated[:, 1] * (
            self.printable_height_mm / self.coverage_height_m
        )
        return np.column_stack((x, y))

    def transform_lonlat(self, latitudes: np.ndarray, longitudes: np.ndarray) -> np.ndarray:
        """Project latitude/longitude arrays and transform them into print mm."""
        projected = project_lonlat_array(
            np.asarray(latitudes, dtype=float),
            np.asarray(longitudes, dtype=float),
            self.center_lat,
            self.center_lon,
        )
        return self.transform_projected(projected)

    def transform_points(self, points: Sequence[object]) -> np.ndarray:
        """Project point objects having latitude/longitude attributes to print mm."""
        return self.transform_projected(project_points(points, self.center_lat, self.center_lon))


def _rotate(coordinates: np.ndarray, degrees: float) -> np.ndarray:
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    matrix = np.array([[cosine, -sine], [sine, cosine]], dtype=float)
    return coordinates @ matrix.T
