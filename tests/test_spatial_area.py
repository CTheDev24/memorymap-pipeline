"""Checks for spatial area indexing without changing clearance decisions."""

import numpy as np
import pytest
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from memorymap_pipeline.spatial_area import AreaMask


def test_partitioned_mask_matches_original_overlap_decisions():
    mask = unary_union([
        box(0, 0, 12, 12).difference(box(1, 1, 11, 11)),
        box(5.9, -3, 6.1, 15), box(-3, 5.9, 15, 6.1),
        box(30, 30, 31, 31),
    ])
    index = AreaMask(mask, maximum_coordinates=10)
    assert len(index.pieces) > 1
    assert unary_union(index.pieces).symmetric_difference(mask).area < 1e-12
    queries = [box(x, y, x + .3, y + .5)
               for x, y in np.random.default_rng(42).uniform(-4, 35, (300, 2))]
    queries += [box(1, 1, 5, 5), box(12, 0, 13, 1), Polygon(),
                box(0, 0, 1e-5, 1e-5), box(0, 0, 1e-5, 1.001e-5)]
    for polygon in queries:
        for threshold in (0.0, 1e-10, .01):
            assert index.exceeds(polygon, threshold) == (polygon.intersection(mask).area > threshold)


@pytest.mark.parametrize('mask', [Polygon(), box(0, 0, 1, 1)])
def test_empty_and_single_piece_masks(mask):
    index = AreaMask(mask)
    for polygon in (Polygon(), box(-1, -1, 2, 2), box(4, 4, 5, 5)):
        assert index.exceeds(polygon, 1e-10) == (polygon.intersection(mask).area > 1e-10)

def test_grouping_matches_global_mask_with_dense_barriers(monkeypatch):
    import memorymap_pipeline.building_grouping as grouping

    rng = np.random.default_rng(17)
    footprints = [box(x, y, x + .35, y + .35)
                  for x, y in rng.uniform(0, 8, (90, 2))]
    barriers = unary_union([box(3.9, -1, 4.1, 9),
                            box(-1, 3.9, 9, 4.1),
                            box(6, 6, 7, 7).difference(box(6.2, 6.2, 6.8, 6.8))])
    eligible = set(range(85))

    class OriginalMask:
        def __init__(self, geometry):
            self.geometry = geometry

        def exceeds(self, polygon, threshold):
            return polygon.intersection(self.geometry).area > threshold

    monkeypatch.setattr(grouping, 'AreaMask', OriginalMask)
    before_diagnostics = {}
    before = grouping.group_footprints(
        footprints, eligible, barriers, diagnostics=before_diagnostics,
        allowed_region=box(0, 0, 8.5, 8.5),
    )
    monkeypatch.setattr(grouping, 'AreaMask', lambda g: AreaMask(g, maximum_coordinates=24))
    after_diagnostics = {}
    after = grouping.group_footprints(
        footprints, eligible, barriers, diagnostics=after_diagnostics,
        allowed_region=box(0, 0, 8.5, 8.5),
    )
    assert len(before) > 0
    assert [(g.members, g.geometry.wkb) for g in before] == [
        (g.members, g.geometry.wkb) for g in after
    ]
    assert before_diagnostics == after_diagnostics
