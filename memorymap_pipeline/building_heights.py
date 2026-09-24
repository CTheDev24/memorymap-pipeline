"""Explicit building-height controls, independent of terrain and legacy ceilings."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BuildingHeightPolicy:
    mode: str = "architectural_relief"
    multiplier: float = 1.0
    minimum_mm: float | None = None
    maximum_mm: float | None = None

    def __post_init__(self):
        if self.mode not in {"architectural_relief", "proportional"}:
            raise ValueError("Unsupported building height mode")
        if not math.isfinite(self.multiplier) or self.multiplier <= 0:
            raise ValueError("Building height multiplier must be positive and finite")
        for value in (self.minimum_mm, self.maximum_mm):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError("Enabled building height limits must be positive and finite")
        if (self.minimum_mm is not None and self.maximum_mm is not None
                and self.minimum_mm > self.maximum_mm):
            raise ValueError("Minimum building height must not exceed maximum building height")

    @classmethod
    def from_config(cls, config):
        return cls(
            str(config.get("building_height_mode", "architectural_relief")),
            float(config.get("building_height_multiplier", 1.0)),
            None if config.get("building_height_min_mm") is None else float(config["building_height_min_mm"]),
            None if config.get("building_height_max_mm") is None else float(config["building_height_max_mm"]),
        )

    def config(self):
        return {
            "building_height_mode": self.mode,
            "building_height_multiplier": self.multiplier,
            "building_height_min_mm": self.minimum_mm,
            "building_height_max_mm": self.maximum_mm,
        }

    @property
    def active(self):
        return self != BuildingHeightPolicy()

    def resolve(self, raw_mm):
        if not math.isfinite(raw_mm) or raw_mm <= 0:
            raise ValueError("Source building height must be positive and finite")
        requested = raw_mm * self.multiplier
        final = requested
        if self.minimum_mm is not None:
            final = max(final, self.minimum_mm)
        if self.maximum_mm is not None:
            final = min(final, self.maximum_mm)
        if not math.isfinite(final):
            raise ValueError("Resolved building height is not finite")
        reason = "floor" if final > requested else "cap" if final < requested else "none"
        return final, reason


def assembly_roots(elements):
    """Connect dependent parts to their overlapping/touching parent footprints.

    Called before parent subtraction. Ordinary neighboring buildings do not form
    assemblies merely because they touch. No semantic class is inferred here.
    """
    from shapely.strtree import STRtree

    footprints = [item[0] for item in elements]
    tree = STRtree(footprints)
    parents = list(range(len(elements)))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for i, (footprint, dims, part, _landmark) in enumerate(elements):
        if not part and dims.min_height_m <= 0:
            continue
        for other in tree.query(footprint, predicate="intersects"):
            j = int(other)
            first, second = root(i), root(j)
            if first != second:
                parents[max(first, second)] = min(first, second)
    return [root(i) for i in range(len(elements))]
