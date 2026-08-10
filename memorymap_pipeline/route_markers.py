from __future__ import annotations

from enum import Enum
from typing import Callable, Iterable

import numpy as np
from shapely.geometry import Point
from trimesh import Trimesh
from trimesh.creation import cylinder


class RouteMarkerMode(str, Enum):
    NONE = "none"
    START = "start"
    FINISH = "finish"
    BOTH = "both"

    @classmethod
    def parse(cls, value: str | "RouteMarkerMode") -> "RouteMarkerMode":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError as exc:
            raise ValueError(f"Unsupported route marker mode: {value!r}") from exc

    @property
    def includes_start(self) -> bool:
        return self in {self.START, self.BOTH}

    @property
    def includes_finish(self) -> bool:
        return self in {self.FINISH, self.BOTH}


SupportSampler = Callable[[np.ndarray, np.ndarray], np.ndarray]


def marker_centers(
    start_xy: Iterable[float],
    finish_xy: Iterable[float],
    mode: str | RouteMarkerMode,
    near_loop_tolerance_mm: float,
) -> list[tuple[str, np.ndarray]]:
    """Select endpoint markers, merging a near-loop into one start marker."""
    selected = RouteMarkerMode.parse(mode)
    if near_loop_tolerance_mm < 0.0:
        raise ValueError("Near-loop tolerance must be non-negative")
    start = np.asarray(tuple(start_xy), dtype=float)
    finish = np.asarray(tuple(finish_xy), dtype=float)
    if start.shape != (2,) or finish.shape != (2,) or not (
        np.all(np.isfinite(start)) and np.all(np.isfinite(finish))
    ):
        raise ValueError("Marker centers must be finite XY coordinates")
    result: list[tuple[str, np.ndarray]] = []
    if selected.includes_start:
        result.append(("start", start))
    if selected.includes_finish:
        if selected.includes_start and np.linalg.norm(finish - start) <= near_loop_tolerance_mm:
            return result
        result.append(("finish", finish))
    return result


def build_route_marker(
    center_xy: Iterable[float],
    support_sampler: SupportSampler,
    printable_region,
    *,
    diameter_mm: float = 3.2,
    visible_height_mm: float = 1.0,
    embed_depth_mm: float = 0.2,
    sections: int = 32,
) -> tuple[Trimesh | None, str | None]:
    """Build a supported cylindrical marker, or explain why it was omitted."""
    center = np.asarray(tuple(center_xy), dtype=float)
    if center.shape != (2,) or not np.all(np.isfinite(center)):
        raise ValueError("Marker center must be a finite XY coordinate")
    if min(diameter_mm, visible_height_mm) <= 0.0 or embed_depth_mm < 0.0:
        raise ValueError("Marker dimensions must be positive and embed depth non-negative")
    if sections < 12:
        raise ValueError("Marker cylinder requires at least 12 sections")

    radius = diameter_mm / 2.0
    footprint = Point(float(center[0]), float(center[1])).buffer(
        radius, quad_segs=max(3, sections // 4)
    )
    if printable_region is None or not printable_region.covers(footprint):
        return None, "marker footprint falls outside the printable region"

    angles = np.linspace(0.0, 2.0 * np.pi, sections, endpoint=False)
    sample_x = np.concatenate(([center[0]], center[0] + radius * np.cos(angles)))
    sample_y = np.concatenate(([center[1]], center[1] + radius * np.sin(angles)))
    support = np.asarray(support_sampler(sample_x, sample_y), dtype=float).reshape(-1)
    if support.size != sections + 1 or not np.all(np.isfinite(support)):
        raise ValueError("Support sampler must return one finite height per sample")

    bottom = float(np.min(support)) - embed_depth_mm
    top = float(np.max(support)) + visible_height_mm
    height = top - bottom
    mesh = cylinder(radius=radius, height=height, sections=sections)
    mesh.apply_translation((float(center[0]), float(center[1]), bottom + height / 2.0))
    mesh.metadata.update(
        {
            "marker_bottom_mm": bottom,
            "marker_top_mm": top,
            "support_min_mm": float(np.min(support)),
            "support_max_mm": float(np.max(support)),
        }
    )
    return mesh, None


__all__ = ["RouteMarkerMode", "build_route_marker", "marker_centers"]
