from __future__ import annotations

import pytest

from memorymap_pipeline.building_classification import BuildingClass, classify_building
from memorymap_pipeline.buildings import _stadium_recipe_for_landmark
from memorymap_pipeline.landmarks import (
    LandmarkRegistry,
    LandmarkRegistryError,
    load_default_landmark_registry,
)


def _document() -> dict:
    return {
        "schema_version": 1,
        "registry_version": "test.1",
        "landmarks": [
            {
                "key": "example-tower",
                "display_name": "Example Tower",
                "aliases": ["Former Tower Name"],
                "identifiers": {
                    "wikidata": ["Q123"],
                    "osm": {"way": [456], "relation": [789]},
                },
                "tag_corrections": {"height": 100.0, "building": "commercial"},
                "enhancement": {
                    "type": "procedural_recipe",
                    "recipe": "stepped_tower",
                    "parameters": {"tiers": 3},
                },
            }
        ],
    }


def test_default_registry_matches_daikin_park_by_stable_wikidata_id() -> None:
    registry = load_default_landmark_registry()
    landmark = registry.match_feature(
        {"name": "Minute Maid Park", "wikidata": "Q1193671"}
    )

    assert landmark is not None
    assert landmark.key == "daikin-park"
    assert "Minute Maid Park" in landmark.aliases
    assert landmark.enhancement is not None
    assert landmark.enhancement.kind == "procedural_recipe"
    assert landmark.enhancement.reference == "stadium_closed_roof"

    corrected_tags = {"wikidata": "Q1193671", **landmark.tag_corrections}
    assert classify_building(corrected_tags) is BuildingClass.STADIUM_ARENA
    recipe = _stadium_recipe_for_landmark(landmark)
    assert recipe is not None
    assert recipe.roof_style == "closed"
    assert recipe.closed_roof_band_count == 3
    assert recipe.closed_roof_band_width_mm == pytest.approx(1.2)
    assert recipe.closed_roof_band_height_mm == pytest.approx(0.32)


def test_legacy_retractable_stadium_recipe_remains_supported() -> None:
    document = _document()
    document["landmarks"][0]["enhancement"] = {
        "type": "procedural_recipe",
        "recipe": "stadium_retractable_roof",
        "parameters": {
            "roof_coverage": 0.44,
            "roof_orientation_degrees": 18.0,
        },
    }
    landmark = LandmarkRegistry.from_dict(document).get("example-tower")

    recipe = _stadium_recipe_for_landmark(landmark)
    assert recipe is not None
    assert recipe.roof_style == "retractable"
    assert recipe.roof_coverage == pytest.approx(0.44)
    assert recipe.roof_orientation_degrees == pytest.approx(18.0)


def test_registry_matches_each_supported_stable_identifier() -> None:
    registry = LandmarkRegistry.from_dict(_document())

    assert registry.match(wikidata="Q123").key == "example-tower"
    assert registry.match(osm_type="way", osm_id=456).key == "example-tower"
    assert registry.match(osm_type="relation", osm_id="789").key == "example-tower"


def test_unknown_identifier_and_mutable_name_degrade_to_no_match() -> None:
    registry = LandmarkRegistry.from_dict(_document())

    assert registry.match(wikidata="Q999") is None
    assert registry.match_feature({"name": "Example Tower"}) is None
    assert registry.get("missing") is None


def test_definition_exposes_corrections_and_recipe_metadata() -> None:
    landmark = LandmarkRegistry.from_dict(_document()).get("example-tower")

    assert landmark is not None
    assert landmark.tag_corrections["height"] == 100.0
    assert landmark.enhancement is not None
    assert landmark.enhancement.metadata == {"tiers": 3}


def test_curated_mesh_metadata_is_supported() -> None:
    document = _document()
    document["landmarks"][0]["enhancement"] = {
        "type": "curated_mesh",
        "resource": "landmarks/example-tower.glb",
        "format": "glb",
        "sha256": "abc123",
    }
    landmark = LandmarkRegistry.from_dict(document).get("example-tower")

    assert landmark is not None
    assert landmark.enhancement is not None
    assert landmark.enhancement.kind == "curated_mesh"
    assert landmark.enhancement.reference == "landmarks/example-tower.glb"
    assert landmark.enhancement.metadata["format"] == "glb"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda doc: doc.update(schema_version=99), "schema version"),
        (
            lambda doc: doc["landmarks"][0]["identifiers"].update(
                wikidata=["not-an-id"]
            ),
            "Wikidata",
        ),
        (
            lambda doc: doc["landmarks"][0].update(
                identifiers={"wikidata": [], "osm": {}}
            ),
            "Wikidata or OSM",
        ),
        (
            lambda doc: doc["landmarks"][0].update(
                enhancement={"type": "curated_mesh", "resource": "../unsafe.stl"}
            ),
            "package-relative",
        ),
    ],
)
def test_invalid_schema_is_rejected(mutation, message: str) -> None:
    document = _document()
    mutation(document)

    with pytest.raises(LandmarkRegistryError, match=message):
        LandmarkRegistry.from_dict(document)


def test_duplicate_stable_identifier_is_rejected() -> None:
    document = _document()
    duplicate = dict(document["landmarks"][0])
    duplicate["key"] = "another-landmark"
    duplicate["display_name"] = "Another Landmark"
    document["landmarks"].append(duplicate)

    with pytest.raises(LandmarkRegistryError, match="Duplicate Wikidata identifier"):
        LandmarkRegistry.from_dict(document)


def test_conflicting_identifiers_are_rejected_at_match_time() -> None:
    document = _document()
    document["landmarks"].append(
        {
            "key": "second-landmark",
            "display_name": "Second Landmark",
            "identifiers": {"wikidata": ["Q555"], "osm": {"way": [999]}},
        }
    )
    registry = LandmarkRegistry.from_dict(document)

    with pytest.raises(LandmarkRegistryError, match="different landmark"):
        registry.match(wikidata="Q123", osm_type="way", osm_id=999)
