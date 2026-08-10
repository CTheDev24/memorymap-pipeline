"""Small, serialization-friendly metrics for landscape surface golden tests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np


@dataclass(frozen=True)
class GroundCoverSignature:
    print_area_mm2: float
    vegetation_area_mm2: float
    exposed_area_mm2: float
    water_area_mm2: float

    def __post_init__(self) -> None:
        values = tuple(asdict(self).values())
        if not all(np.isfinite(values)) or self.print_area_mm2 <= 0.0:
            raise ValueError("Ground-cover signature values must be finite and print area positive")
        if min(values[1:]) < 0.0:
            raise ValueError("Ground-cover areas must be non-negative")

    @property
    def vegetation_fraction(self) -> float:
        return self.vegetation_area_mm2 / self.print_area_mm2

    @property
    def exposed_fraction(self) -> float:
        return self.exposed_area_mm2 / self.print_area_mm2

    @property
    def water_fraction(self) -> float:
        return self.water_area_mm2 / self.print_area_mm2

    def to_dict(self) -> dict[str, float]:
        return {key: float(value) for key, value in asdict(self).items()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GroundCoverSignature":
        return cls(**{field: float(value[field]) for field in cls.__dataclass_fields__})


def upward_projected_area_mm2(mesh: Any | None) -> float:
    """Return XY footprint area from outward-facing top triangles.

    This remains stable when terrain Z or vertical exaggeration changes and does
    not count sidewalls or bottom faces. Surface bodies must use outward winding,
    as MemoryMap's exported watertight bodies do.
    """
    if mesh is None or len(mesh.faces) == 0:
        return 0.0
    triangles = np.asarray(mesh.triangles, dtype=float)
    cross_z = np.cross(
        triangles[:, 1] - triangles[:, 0],
        triangles[:, 2] - triangles[:, 0],
    )[:, 2]
    return float(np.maximum(cross_z, 0.0).sum() / 2.0)


def ground_cover_signature(
    *,
    print_width_mm: float,
    print_height_mm: float,
    vegetation_mesh: Any | None,
    exposed_mesh: Any | None,
    water_mesh: Any | None,
) -> GroundCoverSignature:
    if min(print_width_mm, print_height_mm) <= 0.0:
        raise ValueError("Print dimensions must be positive")
    return GroundCoverSignature(
        print_area_mm2=float(print_width_mm * print_height_mm),
        vegetation_area_mm2=upward_projected_area_mm2(vegetation_mesh),
        exposed_area_mm2=upward_projected_area_mm2(exposed_mesh),
        water_area_mm2=upward_projected_area_mm2(water_mesh),
    )


def assert_signature_within_bands(
    actual: GroundCoverSignature,
    bands: Mapping[str, tuple[float, float]],
) -> None:
    """Assert compact golden fraction bands without requiring source 3MF files."""
    fractions = {
        "vegetation_fraction": actual.vegetation_fraction,
        "exposed_fraction": actual.exposed_fraction,
        "water_fraction": actual.water_fraction,
    }
    for name, (minimum, maximum) in bands.items():
        if name not in fractions:
            raise ValueError(f"Unknown ground-cover metric: {name}")
        value = fractions[name]
        if not minimum <= value <= maximum:
            raise AssertionError(
                f"{name}={value:.4f} outside golden band [{minimum:.4f}, {maximum:.4f}]"
            )


__all__ = [
    "GroundCoverSignature",
    "assert_signature_within_bands",
    "ground_cover_signature",
    "upward_projected_area_mm2",
]
