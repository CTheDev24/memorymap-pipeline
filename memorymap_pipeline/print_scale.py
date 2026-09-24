"""Physical scale and advisory printer thresholds; no geometry policy is applied."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .map_frame import MapFrame


PRINT_SCALE_DEFAULTS = {
    "nozzle_diameter_mm": 0.4,
    "line_width_mm": None,
    "layer_height_mm": None,
    "building_generalization_mode": "manual",
    "building_marginal_width_ratio": 1.25,
    "building_robust_width_ratio": 2.0,
    "building_simplify_tolerance_ratio": 0.375,
    "building_merge_gap_ratio": 1.0,
    "building_group_span_ratio": 10.0,
    "building_route_clearance_ratio": 2.0,
}


def printer_thresholds(config: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve and validate partial/legacy configuration with one set of defaults."""
    values = {**PRINT_SCALE_DEFAULTS, **config}
    if values["building_generalization_mode"] not in {"manual", "print_optimized"}:
        raise ValueError("building_generalization_mode must be manual or print_optimized")

    def number(key: str, *, optional: bool = False, zero: bool = False):
        value = values[key]
        if optional and value is None:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be numeric") from exc
        if (
            isinstance(value, bool)
            or not math.isfinite(result)
            or (result < 0 if zero else result <= 0)
        ):
            raise ValueError(f"{key} must be finite and {'nonnegative' if zero else 'positive'}")
        return result

    nozzle = number("nozzle_diameter_mm", optional=True)
    line = number("line_width_mm", optional=True)
    layer = number("layer_height_mm", optional=True)
    resolution = line if line is not None else nozzle
    if resolution is None:
        raise ValueError("A positive line_width_mm or nozzle_diameter_mm is required")
    result = {
        "nozzle_diameter_mm": nozzle,
        "line_width_mm": line,
        "layer_height_mm": layer,
        "xy_resolution_mm": resolution,
    }
    for name in (
        "marginal_width",
        "robust_width",
        "simplify_tolerance",
        "merge_gap",
        "group_span",
        "route_clearance",
    ):
        threshold = resolution * number(f"building_{name}_ratio", zero=True)
        if not math.isfinite(threshold):
            raise ValueError(f"Derived {name}_mm must be finite")
        result[f"{name}_mm"] = threshold
    if result["robust_width_mm"] < result["marginal_width_mm"]:
        raise ValueError("robust width must be at least marginal width")
    if result["group_span_mm"] < result["merge_gap_mm"]:
        raise ValueError("group span must be at least merge gap")
    if values["building_generalization_mode"] == "print_optimized" and any(
        result[key] <= 0 for key in ("robust_width_mm", "merge_gap_mm", "group_span_mm")
    ):
        raise ValueError("Print-optimized width, merge gap and span must be positive")
    return result


@dataclass(frozen=True)
class PrintScaleContext:
    mm_per_meter: float
    meters_per_mm: float
    x_mm_per_meter: float
    y_mm_per_meter: float
    nozzle_diameter_mm: float | None
    line_width_mm: float | None
    layer_height_mm: float | None
    xy_resolution_mm: float
    marginal_width_mm: float
    robust_width_mm: float
    simplify_tolerance_mm: float
    merge_gap_mm: float
    group_span_mm: float
    route_clearance_mm: float

    @classmethod
    def from_frame(cls, frame: MapFrame, config: Mapping[str, Any]) -> PrintScaleContext:
        return cls(
            mm_per_meter=frame.mm_per_meter,
            meters_per_mm=frame.meters_per_mm,
            x_mm_per_meter=frame.x_mm_per_meter,
            y_mm_per_meter=frame.y_mm_per_meter,
            **printer_thresholds(config),
        )

    @classmethod
    def from_transform(
        cls, transform: Mapping[str, Any], config: Mapping[str, Any]
    ) -> PrintScaleContext:
        """Read the actual legacy CLI transform; never refit the route for diagnostics."""
        if transform.get("map_frame") is not None:
            return cls.from_frame(transform["map_frame"], config)
        scale = float(transform["scale"])
        if not math.isfinite(scale) or scale <= 0 or not math.isfinite(1 / scale):
            raise ValueError("Transform scale must be finite and positive with finite reciprocal")
        return cls(
            mm_per_meter=scale,
            meters_per_mm=1 / scale,
            x_mm_per_meter=scale,
            y_mm_per_meter=scale,
            **printer_thresholds(config),
        )

    def diagnostics(self) -> dict[str, Any]:
        result = asdict(self)
        result["effective_xy_resolution_mm"] = self.xy_resolution_mm
        result["anisotropic"] = not math.isclose(self.x_mm_per_meter, self.y_mm_per_meter)
        return result
