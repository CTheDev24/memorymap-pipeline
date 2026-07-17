import numpy as np
import pytest
from shapely.geometry import Polygon, box

from memorymap_pipeline.mesh import build_base_plate
from memorymap_pipeline.printability import audit_printability
from memorymap_pipeline.stadiums import StadiumRecipe, build_stadium_mesh


def _irregular_stadium() -> tuple[Polygon, Polygon]:
    outer = Polygon([(6, 8), (43, 5), (56, 15), (53, 37), (37, 46), (12, 42), (3, 27)])
    inner = Polygon([(17, 17), (39, 15), (45, 23), (41, 34), (21, 35), (12, 27)])
    return outer, inner


def _assert_watertight_positive_bodies(mesh) -> None:
    bodies = mesh.split(only_watertight=False)
    assert bodies
    assert all(body.is_watertight for body in bodies)
    assert all(body.is_winding_consistent for body in bodies)
    assert all(body.volume > 0 for body in bodies)


def test_irregular_stadium_builds_printable_terraced_bowl() -> None:
    outer, inner = _irregular_stadium()
    stadium = build_stadium_mesh(outer, inner_opening=inner)
    base = build_base_plate(65, 55, 1.6)
    _assert_watertight_positive_bodies(stadium)
    report = audit_printability({"base": base, "buildings": stadium})
    assert report.printable, report.issues
    assert np.isclose(stadium.bounds[0, 2], 0.0)
    assert np.isclose(stadium.bounds[1, 2], 4.0)
    assert stadium.metadata["stadium_recipe"]["tier_count"] == 3


@pytest.mark.parametrize("roof_style", ["asymmetric", "retractable"])
def test_supported_roof_options_have_no_floating_shells(roof_style: str) -> None:
    outer, inner = _irregular_stadium()
    recipe = StadiumRecipe(
        roof_style=roof_style,
        roof_orientation_degrees=12,
        roof_height_mm=7.0,
        roof_thickness_mm=0.8,
    )
    stadium = build_stadium_mesh(outer, inner_opening=inner, recipe=recipe)
    base = build_base_plate(65, 55, 1.6)
    _assert_watertight_positive_bodies(stadium)
    report = audit_printability({"base": base, "buildings": stadium})
    assert report.printable, report.issues
    assert np.isclose(stadium.bounds[1, 2], 7.0)
    assert len(stadium.split(only_watertight=False)) >= 5


def test_default_opening_can_be_derived_from_outer_footprint() -> None:
    stadium = build_stadium_mesh(box(5, 5, 45, 35))
    _assert_watertight_positive_bodies(stadium)
    assert stadium.bounds[1, 2] == pytest.approx(4.0)


@pytest.mark.parametrize("band_count", [2, 3])
def test_closed_roof_follows_outer_footprint_and_is_ground_supported(band_count: int) -> None:
    outer, inner = _irregular_stadium()
    recipe = StadiumRecipe(
        roof_style="closed",
        roof_height_mm=6.5,
        roof_orientation_degrees=17,
        closed_roof_band_count=band_count,
    )
    stadium = build_stadium_mesh(outer, inner_opening=inner, recipe=recipe)
    base = build_base_plate(65, 55, 1.6)

    _assert_watertight_positive_bodies(stadium)
    report = audit_printability({"base": base, "buildings": stadium})

    assert report.printable, report.issues
    assert stadium.bounds[0, 2] == pytest.approx(0.0)
    assert stadium.bounds[1, 2] == pytest.approx(6.98)
    assert stadium.bounds[0, 0] == pytest.approx(outer.bounds[0])
    assert stadium.bounds[1, 0] == pytest.approx(outer.bounds[2])
    assert stadium.bounds[0, 1] == pytest.approx(outer.bounds[1])
    assert stadium.bounds[1, 1] == pytest.approx(outer.bounds[3])


def test_closed_roof_rejects_narrow_or_excessive_bands() -> None:
    with pytest.raises(ValueError, match="band width"):
        StadiumRecipe(roof_style="closed", closed_roof_band_width_mm=0.6).validate()
    with pytest.raises(ValueError, match="two or three"):
        StadiumRecipe(roof_style="closed", closed_roof_band_count=4).validate()


def test_tiers_reject_unprintably_narrow_bowl() -> None:
    outer = box(0, 0, 30, 20)
    inner = box(1, 1, 29, 19)
    with pytest.raises(ValueError, match="bowl clearance"):
        build_stadium_mesh(outer, inner_opening=inner)


def test_roof_rejects_sub_nozzle_features() -> None:
    with pytest.raises(ValueError, match="at least 0.8 mm"):
        StadiumRecipe(minimum_feature_mm=0.6).validate()
