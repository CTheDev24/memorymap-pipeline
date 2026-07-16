"""Tests for print-aware building classification."""

import pytest

from memorymap_pipeline.building_classification import (
    BUILDING_PRESETS,
    BuildingClass,
    DetailBehavior,
    classify_building,
    preset_for,
)
from memorymap_pipeline.buildings import _building_dimensions


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"building": "apartments"}, BuildingClass.RESIDENTIAL),
        ({"building": "office"}, BuildingClass.COMMERCIAL_OFFICE),
        ({"building": "warehouse"}, BuildingClass.INDUSTRIAL_WAREHOUSE),
        ({"building": "retail"}, BuildingClass.RETAIL),
        ({"building": "parking"}, BuildingClass.PARKING),
        ({"amenity": "hospital", "building": "yes"}, BuildingClass.CIVIC_INSTITUTIONAL),
        ({"leisure": "stadium", "building": "yes"}, BuildingClass.STADIUM_ARENA),
        ({"amenity": "place_of_worship"}, BuildingClass.RELIGIOUS),
        (
            {"tourism": "attraction", "wikidata": "Q123", "building": "yes"},
            BuildingClass.LANDMARK,
        ),
        ({"historic": "warehouse", "building": "warehouse"}, BuildingClass.INDUSTRIAL_WAREHOUSE),
        ({"building": "yes"}, BuildingClass.UNKNOWN),
    ],
)
def test_osm_tag_classification(tags: dict[str, str], expected: BuildingClass) -> None:
    assert classify_building(tags) is expected


def test_priority_is_stable_for_conflicting_mixed_use_tags() -> None:
    assert classify_building({"building": "apartments", "amenity": "parking"}) is BuildingClass.PARKING
    assert (
        classify_building({"building": "cathedral", "landmark": "yes"})
        is BuildingClass.LANDMARK
    )


def test_semicolon_values_and_case_are_normalized() -> None:
    assert (
        classify_building({"building": "Yes", "office": "Government;Company"})
        is BuildingClass.COMMERCIAL_OFFICE
    )


def test_explicit_override_is_deterministic() -> None:
    assert (
        classify_building({"memorymap:class": "stadium", "historic": "yes"})
        is BuildingClass.STADIUM_ARENA
    )
    assert classify_building({"memorymap:class": "not-a-class"}) is BuildingClass.UNKNOWN


def test_presets_cover_every_class_and_have_sane_dimensions() -> None:
    assert set(BUILDING_PRESETS) == set(BuildingClass)
    for preset in BUILDING_PRESETS.values():
        assert preset.fallback_levels > 0
        assert preset.floor_height_m > 0
        assert preset.fallback_height_m > 0
        assert preset.min_printable_height_mm >= 0.8
        assert preset.max_visual_height_mm >= preset.min_printable_height_mm
        assert preset.roof_preferences


def test_landmark_and_stadium_preserve_parts_and_detail() -> None:
    for category in (BuildingClass.LANDMARK, BuildingClass.STADIUM_ARENA):
        preset = preset_for(category)
        assert preset.detail_behavior is DetailBehavior.PRESERVE
        assert not preset.merge_building_parts


def test_preset_can_be_resolved_directly_from_tags() -> None:
    preset = preset_for({"building": "industrial"})
    assert preset is BUILDING_PRESETS[BuildingClass.INDUSTRIAL_WAREHOUSE]
    assert preset.detail_behavior is DetailBehavior.SIMPLIFY


def test_class_presets_supply_missing_real_world_height() -> None:
    warehouse = _building_dimensions(
        {"building": "warehouse"},
        default_height_m=6.0,
        levels_to_m=3.0,
        max_height_m=400.0,
    )
    office = _building_dimensions(
        {"building": "office"},
        default_height_m=6.0,
        levels_to_m=3.0,
        max_height_m=400.0,
    )

    assert warehouse.total_height_m == pytest.approx(6.0)
    assert office.total_height_m == pytest.approx(21.6)
    assert office.total_height_m > warehouse.total_height_m


def test_explicit_height_remains_authoritative_over_class_preset() -> None:
    dimensions = _building_dimensions(
        {"building": "warehouse", "height": "14.5"},
        default_height_m=6.0,
        levels_to_m=3.0,
        max_height_m=400.0,
    )

    assert dimensions.total_height_m == pytest.approx(14.5)
