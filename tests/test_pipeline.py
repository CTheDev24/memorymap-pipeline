from pathlib import Path
import json
import sys

import pytest
from shapely.geometry import box

from memorymap_pipeline.buildings import _extract_real_height_m
from memorymap_pipeline.cli import main
from memorymap_pipeline.config import DEFAULT_CONFIG, load_config
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.mesh import embedded_feature_dimensions
from memorymap_pipeline.projection import normalize_and_scale_points, project_points


def test_parse_and_scale_route(tmp_path: Path) -> None:
    gpx_text = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<gpx version=\"1.1\" creator=\"test\">
  <trk>
    <trkseg>
      <trkpt lat=\"40.7128\" lon=\"-74.0060\"><ele>10</ele></trkpt>
      <trkpt lat=\"40.7129\" lon=\"-74.0061\"><ele>11</ele></trkpt>
      <trkpt lat=\"40.7130\" lon=\"-74.0062\"><ele>12</ele></trkpt>
    </trkseg>
  </trk>
</gpx>
"""
    gpx_path = tmp_path / "sample.gpx"
    gpx_path.write_text(gpx_text, encoding="utf-8")

    route = load_route_from_gpx(gpx_path)
    assert len(route.points) == 3

    projected = project_points(route.points, center_lat=route.points[0].latitude, center_lon=route.points[0].longitude)
    scaled = normalize_and_scale_points(projected, width_mm=241.0, height_mm=190.0)

    assert scaled.shape == (3, 2)
    assert scaled[:, 0].min() >= 0.0
    assert scaled[:, 0].max() <= 241.0
    assert scaled[:, 1].min() >= 0.0
    assert scaled[:, 1].max() <= 190.0


def test_load_config_does_not_mutate_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"portrait": {"map_width": 123.0}}), encoding="utf-8")
    assert load_config(config_path)["portrait"]["map_width"] == 123.0
    assert DEFAULT_CONFIG["portrait"]["map_width"] == 190.0
    assert load_config()["portrait"]["map_width"] == 190.0


def test_buildings_run_when_roads_are_disabled(tmp_path: Path, monkeypatch) -> None:
    gpx_path = tmp_path / "route.gpx"
    gpx_path.write_text(
        """<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="40.0" lon="-74.0"/><trkpt lat="40.001" lon="-74.002"/>
</trkseg></trk></gpx>""",
        encoding="utf-8",
    )
    call = {}

    def fake_buildings(**kwargs):
        call.update(kwargs)
        return None, None

    monkeypatch.setattr("memorymap_pipeline.cli.download_and_build_buildings", fake_buildings)
    monkeypatch.setattr("memorymap_pipeline.cli.export_3mf", lambda *args: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["memorymap-pipeline", str(gpx_path), str(tmp_path / "out.3mf"), "--no-roads", "--no-base"],
    )

    main()

    assert call["bbox"] == pytest.approx((40.0, 40.001, -74.002, -74.0))


def test_cli_overlay_layers_embed_below_base_top(tmp_path: Path, monkeypatch) -> None:
    gpx_path = tmp_path / "route.gpx"
    gpx_path.write_text(
        """<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="40.0" lon="-74.0"/><trkpt lat="40.001" lon="-74.002"/>
</trkseg></trk></gpx>""",
        encoding="utf-8",
    )

    captured: dict[str, float] = {}

    class DummyMesh:
        def apply_translation(self, _offset):
            pass

    def fake_route_mesh_from_polygon(_polygon, height_mm, z_offset=0.0):
        captured["z_offset"] = z_offset
        captured["height_mm"] = height_mm
        return DummyMesh()

    monkeypatch.setattr("memorymap_pipeline.cli.route_mesh_from_polygon", fake_route_mesh_from_polygon)
    monkeypatch.setattr("memorymap_pipeline.cli.download_and_build_roads", lambda **kwargs: (None, None))
    monkeypatch.setattr("memorymap_pipeline.cli.download_and_build_buildings", lambda **kwargs: (None, None))
    monkeypatch.setattr("memorymap_pipeline.cli.center_meshes_to_base", lambda *args, **kwargs: None)
    monkeypatch.setattr("memorymap_pipeline.cli.export_3mf", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["memorymap-pipeline", str(gpx_path), str(tmp_path / "out.3mf")],
    )

    main()

    assert captured["z_offset"] == pytest.approx(-0.2)
    assert captured["height_mm"] == pytest.approx(2.2)


def test_embedded_feature_height_remains_visible_above_base() -> None:
    extrusion, bottom_z, embed = embedded_feature_dimensions(0.6, 1.0, 0.2)
    assert embed == pytest.approx(0.2)
    assert bottom_z == pytest.approx(-0.2)
    assert extrusion == pytest.approx(0.8)
    assert bottom_z + extrusion == pytest.approx(0.6)


def test_feature_embed_is_clamped_to_base_and_disabled_without_base() -> None:
    assert embedded_feature_dimensions(0.6, 0.1, 0.2) == pytest.approx((0.7, -0.1, 0.1))
    assert embedded_feature_dimensions(0.6, 0.0, 0.2) == pytest.approx((0.6, 0.0, 0.0))


# ---------------------------------------------------------------------------
# _extract_real_height_m
# ---------------------------------------------------------------------------

_DEFAULTS = dict(default_height_m=6.0, levels_to_m=3.0, max_height_m=400.0)


def test_height_from_explicit_tag():
    assert _extract_real_height_m({"height": "50"}, **_DEFAULTS) == pytest.approx(50.0)


def test_height_tag_strips_units():
    assert _extract_real_height_m({"height": "238.4 m"}, **_DEFAULTS) == pytest.approx(238.4)


def test_height_tag_feet_converted_to_meters():
    # 100 ft = 30.48 m
    result = _extract_real_height_m({"height": "100ft"}, **_DEFAULTS)
    assert result == pytest.approx(100 * 0.3048, rel=1e-6)


def test_height_tag_above_max_falls_back_to_levels():
    # 500 m exceeds max_height_m=400, so should fall back to levels
    tags = {"height": "500", "building:levels": "10"}
    assert _extract_real_height_m(tags, **_DEFAULTS) == pytest.approx(30.0)


def test_height_tag_above_max_no_levels_uses_default():
    tags = {"height": "500"}
    assert _extract_real_height_m(tags, **_DEFAULTS) == pytest.approx(6.0)


def test_height_from_levels():
    assert _extract_real_height_m({"building:levels": "20"}, **_DEFAULTS) == pytest.approx(60.0)


def test_height_levels_list_value():
    # osmnx can return list values for some tags
    assert _extract_real_height_m({"building:levels": ["5"]}, **_DEFAULTS) == pytest.approx(15.0)


def test_height_corrupt_tag_falls_back():
    assert _extract_real_height_m({"height": "not_a_number"}, **_DEFAULTS) == pytest.approx(6.0)


def test_height_no_tags_returns_default():
    assert _extract_real_height_m({}, **_DEFAULTS) == pytest.approx(6.0)


def test_height_zero_not_used():
    # Zero-height tags should be ignored and fall back
    tags = {"height": "0", "building:levels": "0"}
    assert _extract_real_height_m(tags, **_DEFAULTS) == pytest.approx(6.0)


def test_height_uses_roof_levels_when_main_levels_are_missing():
    assert _extract_real_height_m({"roof:levels": "2"}, **_DEFAULTS) == pytest.approx(12.0)


def test_height_uses_conservative_building_type_estimate():
    assert _extract_real_height_m({"building": "apartments"}, **_DEFAULTS) == pytest.approx(12.0)
    assert _extract_real_height_m({"building": "garage"}, **_DEFAULTS) == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Proportional height scaling
# ---------------------------------------------------------------------------

def test_proportional_scale_tallest_at_max():
    """Tallest building in a scene should print at exactly max_print_height_mm."""
    max_print_mm = 31.75
    real_heights = [10.0, 50.0, 200.0]  # metres
    max_real = max(real_heights)
    scale = max_print_mm / max_real
    extrusions = [h * scale for h in real_heights]
    assert extrusions[-1] == pytest.approx(max_print_mm)
    # Shorter buildings scale proportionally
    assert extrusions[0] == pytest.approx(10.0 / 200.0 * max_print_mm)


def test_proportional_scale_uniform_heights():
    """When all buildings have the same height they all print at max_print_height_mm."""
    max_print_mm = 31.75
    real_heights = [6.0, 6.0, 6.0]
    max_real = max(real_heights)
    scale = max_print_mm / max_real
    extrusions = [max(0.4, h * scale) for h in real_heights]
    assert all(e == pytest.approx(max_print_mm) for e in extrusions)


def test_min_building_height_floor():
    """Very short building should be clamped to min_building_height_mm."""
    min_mm = 0.4
    max_print_mm = 31.75
    real_heights = [1.0, 200.0]
    scale = max_print_mm / max(real_heights)
    extrusions = [max(min_mm, h * scale) for h in real_heights]
    # 1 m building: 1 * (31.75/200) = 0.159 mm → clamped to 0.4
    assert extrusions[0] == pytest.approx(min_mm)
    assert extrusions[1] == pytest.approx(max_print_mm)


# ---------------------------------------------------------------------------
# Clip / omit threshold
# ---------------------------------------------------------------------------

def _clip_fraction(building_poly, plate_box, clip_threshold: float):
    """Helper replicating the clip/omit logic from buildings.py."""
    original_area = building_poly.area
    clipped = building_poly.intersection(plate_box)
    if clipped.is_empty or original_area <= 0:
        return None
    fraction_inside = clipped.area / original_area
    if fraction_inside < (1.0 - clip_threshold):
        return None  # omitted
    return clipped


def test_building_fully_inside_kept():
    plate = box(8, 8, 232, 182)  # margin-inset 240x190 plate with 8 mm margin
    building = box(50, 50, 70, 70)
    result = _clip_fraction(building, plate, clip_threshold=0.5)
    assert result is not None
    assert result.area == pytest.approx(building.area)


def test_building_mostly_outside_omitted():
    plate = box(8, 8, 232, 182)
    # Building centered at x=0, only ~10% inside plate
    building = box(-90, 50, 10, 70)
    result = _clip_fraction(building, plate, clip_threshold=0.5)
    assert result is None


def test_building_half_inside_kept_and_clipped():
    plate = box(8, 8, 232, 182)
    # Building straddles left edge of plate: exactly half inside
    building = box(0, 50, 16, 70)  # 8 mm inside (x=8..16), 8 mm outside (x=0..8)
    result = _clip_fraction(building, plate, clip_threshold=0.5)
    assert result is not None
    # Clipped area should be approximately half the original
    assert result.area == pytest.approx(building.area / 2.0, rel=0.01)


def test_building_entirely_outside_omitted():
    plate = box(8, 8, 232, 182)
    building = box(-50, -50, -10, -10)
    result = _clip_fraction(building, plate, clip_threshold=0.5)
    assert result is None

