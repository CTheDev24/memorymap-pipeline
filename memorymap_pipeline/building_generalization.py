"""Read-only print-space measurements and future disposition vocabulary."""

from __future__ import annotations

import math
from enum import Enum

import numpy as np
from shapely.errors import ShapelyError

from .print_scale import PrintScaleContext


class BuildingDisposition(str, Enum):
    PRESERVED = "preserved"
    SIMPLIFIED = "simplified"
    GROUPED = "grouped"
    TYPIFIED = "typified"
    OMITTED = "omitted"


WIDTH_TOLERANCE_MM = 0.0001


def has_local_core(polygon, width_mm: float) -> bool:
    """Same mitred inward-buffer core concept as the existing grouping screen.

    This is maximum surviving core width, not minimum neck width or a guarantee
    that all tips/walls survive slicing. No geometry returned here is manufactured.
    """
    radius = max(0.0, width_mm / 2 - WIDTH_TOLERANCE_MM)
    return not polygon.buffer(-radius, join_style=2).is_empty


def local_core_width_mm(polygon) -> float:
    """Binary-search the collapse width to 0.0001 mm using mitred erosion.

    Bounds only bracket the search; they are never used as the width metric.
    Holes and multipart geometry participate in every erosion.
    """
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ValueError("Local width requires valid nonempty polygonal geometry")
    x0, y0, x1, y1 = polygon.bounds
    low, high = 0.0, min(x1 - x0, y1 - y0)
    for _ in range(48):
        if high - low <= WIDTH_TOLERANCE_MM:
            break
        middle = (low + high) / 2
        if polygon.buffer(-middle / 2, join_style=2).is_empty:
            high = middle
        else:
            low = middle
    return (low + high) / 2


def measure_footprints(footprints, context: PrintScaleContext) -> dict:
    widths = []
    marginal = robust = failed = 0
    for polygon in footprints:
        try:
            width = local_core_width_mm(polygon)
            below_marginal = not has_local_core(polygon, context.marginal_width_mm)
            below_robust = not has_local_core(polygon, context.robust_width_mm)
            if not math.isfinite(width):
                raise ValueError("Nonfinite width")
        except (ShapelyError, ValueError, ArithmeticError):
            # Diagnostic failures must not change generation or count as tiny buildings.
            failed += 1
            continue
        widths.append(width)
        marginal += below_marginal
        robust += below_robust
    widths.sort()
    count = len(widths)
    result = {
        "width_method": "maximum_surviving_mitred_erosion_core_v1",
        "width_tolerance_mm": WIDTH_TOLERANCE_MM,
        "width_population": "clipped ordinary footprints after building-part subtraction, before grouping/filtering",
        "measured_building_count": count,
        "width_measurement_failed_count": failed,
        "buildings_below_marginal_width": marginal,
        "buildings_below_marginal_width_percent": 100 * marginal / count if count else None,
        "buildings_below_robust_width": robust,
        "buildings_below_robust_width_percent": 100 * robust / count if count else None,
    }
    for percentile in (10, 50, 90):
        result[f"building_width_p{percentile}_mm"] = (
            float(np.percentile(widths, percentile)) if count else None
        )
    return result


def disposition_diagnostics(records: list[dict]) -> dict:
    """Counts use existing per-source-polygon records, including explicit omissions."""
    statuses = [record["status"] for record in records]
    for record in records:
        disposition = {
            "retained": BuildingDisposition.PRESERVED,
            "grouped": BuildingDisposition.GROUPED,
            "omitted": BuildingDisposition.OMITTED,
        }.get(record["status"])
        record["disposition"] = disposition.value if disposition else None
    return {
        "source_building_count": len(records),
        "source_count_unit": "source polygon records (multipart sources can contribute multiple)",
        "grouped_source_count": statuses.count("grouped"),
        "group_count": len({r["group_id"] for r in records if r["status"] == "grouped"}),
        "ungrouped_count": statuses.count("retained"),
        "omitted_count": statuses.count("omitted"),
        "unresolved_count": statuses.count("unresolved"),
    }
