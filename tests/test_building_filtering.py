from types import SimpleNamespace

import pytest
from shapely.geometry import box

from memorymap_pipeline.building_classification import BuildingClass
from memorymap_pipeline.building_filtering import residential_omissions
from memorymap_pipeline.building_grouping import FootprintGroup


def element(p, kind=BuildingClass.RESIDENTIAL, part=False, landmark=None):
    return (p, SimpleNamespace(building_class=kind, min_height_m=0), part, landmark)


def test_filter_uses_width_not_area_and_preserves_unknown_buildings():
    elements = [
        element(box(0, 0, 10, 0.3)),
        element(box(20, 0, 20.9, 0.9)),
        element(box(30, 0, 30.2, 0.2), BuildingClass.UNKNOWN),
    ]
    assert residential_omissions(elements, [], 0.8) == {0}
    assert residential_omissions(elements, [], 0) == set()


def test_filter_runs_on_group_footprint_and_keeps_protected_sources():
    elements = [
        element(box(0, 0, 0.2, 0.2)),
        element(box(0.3, 0, 0.5, 0.2)),
        element(box(2, 0, 2.2, 0.2), part=True),
    ]
    groups = [FootprintGroup((0, 1), box(-0.1, -0.3, 0.9, 0.6))]
    assert residential_omissions(elements, groups, 0.8) == set()
    assert residential_omissions(elements, groups, 1.2) == {0, 1}


def test_filter_preserves_courtyards_landmarks_and_parent_part_contact():
    ring = box(0, 0, 1, 1).difference(box(0.1, 0.1, 0.9, 0.9))
    elements = [
        element(ring),
        element(box(2, 0, 2.2, 0.2), landmark=object()),
        element(box(3, 0, 3.2, 0.2)),
        element(box(3.2, 0, 3.4, 0.2), part=True),
    ]
    assert residential_omissions(elements, [], 0.8) == set()


@pytest.mark.parametrize("width", [-1, float("nan"), float("inf")])
def test_filter_rejects_invalid_threshold(width):
    with pytest.raises(ValueError):
        residential_omissions([], [], width)
