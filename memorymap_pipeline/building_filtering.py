"""Conservative residential omission after print-space grouping."""

import math

from shapely.ops import unary_union

from .building_classification import BuildingClass


def residential_omissions(elements, groups, minimum_width_mm):
    if not math.isfinite(minimum_width_mm) or minimum_width_mm < 0:
        raise ValueError("Residential minimum width must be finite and nonnegative")
    if minimum_width_mm == 0:
        return set()
    protected_parts = unary_union(
        [p for p, _d, part, landmark in elements if part or landmark is not None]
    )

    def residential(i):
        p, dims, part, landmark = elements[i]
        return (
            dims.building_class == BuildingClass.RESIDENTIAL
            and not part
            and landmark is None
            and dims.min_height_m == 0
            and p.geom_type == "Polygon"
            and not p.interiors
            and not protected_parts.intersects(p)
        )

    grouped = {i for g in groups for i in g.members}
    candidates = [(g.geometry, g.members) for g in groups]
    candidates.extend((p, (i,)) for i, (p, *_rest) in enumerate(elements) if i not in grouped)
    omitted = set()
    for polygon, members in candidates:
        if (
            all(residential(i) for i in members)
            and polygon.buffer(-max(0, minimum_width_mm / 2 - 1e-6), join_style=2).is_empty
        ):
            omitted.update(members)
    return omitted
