"""Physical geometry and persistence contracts for opt-in height controls."""

import json
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box

from memorymap_pipeline.building_heights import BuildingHeightPolicy
from memorymap_pipeline.buildings import download_and_build_buildings
from memorymap_pipeline.desktop.project import DesktopProject
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.mesh import build_base_plate
from memorymap_pipeline.printability import audit_printability


def generate(tmp_path, policy, tags=None, polygons=None, **kwargs):
    frame = MapFrame(38.8719, -77.0563, 3000, 3000, 100, 100)
    tags = tags or [{"building": "yes", "height": 23.5}]
    polygons = polygons or [box(30, 30, 50, 50)] * len(tags)
    features = []
    for i, (polygon, properties) in enumerate(zip(polygons, tags)):
        xy = np.asarray(polygon.exterior.coords)
        lat, lon = frame.print_to_lonlat(xy[:, 0], xy[:, 1])
        features.append({"type": "Feature", "properties": {"id": str(i), **properties},
                         "geometry": {"type": "Polygon", "coordinates": [list(zip(lon, lat))]}})
    path = tmp_path / "heights.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    records, stats = [], {}
    _, mesh = download_and_build_buildings(
        bbox=None, center_lat=frame.center_lat, center_lon=frame.center_lon,
        transform={"map_frame": frame}, map_width_mm=100, map_height_mm=100, margin_mm=0,
        buildings_file=str(path), height_policy=policy, diagnostics=records,
        generalization_stats=stats, z_offset=-.2, embed_depth_mm=.2, **kwargs,
    )
    assert mesh is not None and mesh.is_watertight and mesh.is_winding_consistent
    return mesh, records, stats


@pytest.mark.parametrize('kwargs', [
    {"mode": "guess"}, {"multiplier": 0}, {"multiplier": float('nan')},
    {"maximum_mm": float('inf')}, {"minimum_mm": 0}, {"minimum_mm": 3, "maximum_mm": 2},
])
def test_invalid_height_policy_rejected(kwargs):
    with pytest.raises(ValueError):
        BuildingHeightPolicy(**kwargs)


@pytest.mark.parametrize('policy, expected', [
    (BuildingHeightPolicy('proportional'), 23.5 / 30),
    (BuildingHeightPolicy('proportional', 2), 47 / 30),
    (BuildingHeightPolicy('proportional', 1, 1.2), 1.2),
    (BuildingHeightPolicy('proportional', 2, None, 1.3), 1.3),
])
def test_proportional_top_includes_roof_and_excludes_embed(tmp_path, policy, expected):
    mesh, records, _ = generate(tmp_path, policy, [
        {'building': 'yes', 'height': 23.5, 'roof:shape': 'gabled', 'roof:height': 3},
    ])
    assert mesh.bounds[1, 2] == pytest.approx(expected)
    assert mesh.bounds[0, 2] == pytest.approx(-.2)
    assert audit_printability({'base': build_base_plate(100, 100, 1.6), 'buildings': mesh}).printable
    assert records[0]['height_control']['effective_tops_mm'] == pytest.approx([expected])


def test_relief_multiplier_and_limits_are_applied_after_legacy_mapping(tmp_path):
    baseline, _, _ = generate(tmp_path, BuildingHeightPolicy())
    changed, _, _ = generate(tmp_path, BuildingHeightPolicy(multiplier=.5, maximum_mm=2))
    assert changed.bounds[1, 2] == pytest.approx(min(2, baseline.bounds[1, 2] * .5))
    assert changed.bounds[:, :2] == pytest.approx(baseline.bounds[:, :2])


def test_limits_scale_parent_part_assembly_together_and_preserve_support(tmp_path):
    tags = [{'building:part': 'yes', 'height': 12},
            {'building:part': 'yes', 'height': 24, 'min_height': 12}]
    mesh, records, stats = generate(tmp_path, BuildingHeightPolicy('proportional', 1, 1.2), tags)
    assert mesh.bounds[1, 2] == pytest.approx(1.2)
    assert records[0]['height_control']['assembly_id'] == records[1]['height_control']['assembly_id']
    assert records[0]['height_control']['effective_tops_mm'] == pytest.approx([.6])
    assert records[1]['height_control']['effective_tops_mm'] == pytest.approx([1.2])
    assert stats['height_controls']['raised_assemblies'] == 1
    assert audit_printability({'base': build_base_plate(100, 100, 1.6), 'buildings': mesh}).printable


def test_stadium_recipe_obeys_final_envelope(tmp_path):
    mesh, records, _ = generate(tmp_path, BuildingHeightPolicy('proportional', 2, None, 1.5),
                                [{'building': 'stadium', 'height': 30}],
                                [box(20, 20, 80, 70)])
    assert mesh.bounds[1, 2] == pytest.approx(1.5)
    assert records[0]['height_control']['recipe_adapter'] == 'whole_recipe_envelope'
    assert audit_printability({'base': build_base_plate(100, 100, 1.6), 'buildings': mesh}).printable


def test_project_height_roundtrip_and_legacy_defaults():
    project = DesktopProject(MapFrame(0, 0, 1000, 1000, 100, 100),
                             building_height_mode='proportional', building_height_multiplier=2,
                             building_height_min_mm=.8, building_height_max_mm=3, color_preset="urban-classic")
    assert DesktopProject.from_json(project.to_json()) == project
    old = project.to_dict()
    old['version'] = 2
    restored = DesktopProject.from_dict(old)
    assert restored.building_height_mode == 'architectural_relief'
    assert restored.building_height_multiplier == 1
    assert restored.building_height_min_mm is None and restored.building_height_max_mm is None


def test_stale_generation_is_not_presented_or_saved(tmp_path):
    from memorymap_pipeline.desktop.window import MemoryMapWindow
    warnings, enabled = [], []
    window = SimpleNamespace(
        _height_revision=2, _generation_height_revision=1, _result_height_revision=1,
        result_path=None, save=SimpleNamespace(setEnabled=enabled.append), add_warning=warnings.append,
    )
    MemoryMapWindow.set_result(window, str(tmp_path / 'old.3mf'))
    assert window.result_path is None and enabled == [False]
    MemoryMapWindow.save_result(window)
    assert len(warnings) == 2


@pytest.fixture
def desktop_window(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication, QWidget

    import memorymap_pipeline.desktop.window as ui

    class Page(QObject):
        def setWebChannel(self, channel):
            pass
        def runJavaScript(self, script):
            pass

    class WebView(QWidget):
        loadFinished = Signal(bool)
        def __init__(self):
            super().__init__()
            self._page = Page(self)
            self.html = ""
        def page(self):
            return self._page
        def settings(self):
            return SimpleNamespace(setAttribute=lambda *_args: None)
        def setHtml(self, html, *_args):
            self.html = html

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(ui, "QWebEngineView", WebView)
    window = ui.MemoryMapWindow()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def test_desktop_restores_height_settings_and_invalidates_old_result(desktop_window, tmp_path):
    from pathlib import Path

    from memorymap_pipeline.gpx_loader import load_route_from_gpx
    window = desktop_window
    route_path = Path("tests/fixtures/frame_route.gpx")
    route = load_route_from_gpx(route_path)
    project = DesktopProject(
        MapFrame(29.76, -95.37, 1000, 1400, 190, 240), gpx_path=str(route_path.resolve()),
        building_height_mode="proportional", building_height_multiplier=2.35,
        building_height_min_mm=.85, building_height_max_mm=2.15, color_preset="urban-classic",
    )
    window._apply_project(project, route_path, route)
    saved = window._project_state()
    assert saved.building_height_multiplier == 2.35
    assert saved.building_height_min_mm == .85
    assert saved.building_height_max_mm == 2.15
    assert saved.frame == project.frame and window.portrait.isChecked()
    assert not window.building_max_height.isEnabled()
    assert window.building_height_min.minimum() == .01
    assert window.building_height_multiplier.minimum() == .01
    assert window._generation_config_payload()["building_height_mode"] == "proportional"
    output = tmp_path / "result.3mf"
    output.write_bytes(b"previous model")
    window._generation_height_revision = window._height_revision
    window.set_result(str(output))
    assert window.save.isEnabled()
    window.building_height_multiplier.setValue(1)
    assert not window.save.isEnabled()
    assert "Regenerate" in window.preview_view.html
    window.building_height_min.setValue(3)
    with pytest.raises(ValueError, match="Minimum"):
        window._generation_config_payload()
    window._reset_building_heights()
    assert window._height_config() == BuildingHeightPolicy().config()
    assert not window.save.isEnabled()


def test_shared_terrain_datum_keeps_stacked_parts_connected(tmp_path):
    tags = [{"building:part": "yes", "height": 12},
            {"building:part": "yes", "height": 24, "min_height": 12}]
    terrain = lambda x, y: .01 * np.asarray(x)
    mesh, records, _ = generate(tmp_path, BuildingHeightPolicy("proportional", 2), tags,
                                [box(30, 30, 60, 60), box(40, 40, 50, 50)], terrain_height_at=terrain)
    assert mesh.bounds[1, 2] == pytest.approx(.3 + 24 / 30 * 2)
    assert mesh.bounds[0, 2] == pytest.approx(.3 - .2)
    assert records[1]["height_control"]["effective_tops_mm"] == pytest.approx([1.6])


def test_replaced_parent_does_not_dilute_final_minimum(tmp_path):
    mesh, records, _ = generate(tmp_path, BuildingHeightPolicy("proportional", 1, 1.2), [
        {"building": "yes", "height": 30}, {"building:part": "yes", "height": 12},
    ])
    assert mesh.bounds[1, 2] == pytest.approx(1.2)
    assert records[1]["height_control"]["effective_tops_mm"] == pytest.approx([1.2])


@pytest.mark.parametrize("mode", ["manual", "print_optimized"])
def test_grouped_buildings_keep_final_limits_and_member_provenance(tmp_path, mode):
    from memorymap_pipeline.print_scale import PrintScaleContext
    frame = MapFrame(38.8719, -77.0563, 3000, 3000, 100, 100)
    mesh, records, _ = generate(
        tmp_path, BuildingHeightPolicy("proportional", 2, .3, .4),
        [{"building": "house", "height": 6}] * 2,
        [box(30, 30, 30.5, 32), box(30.7, 30, 31.2, 32)],
        generalization_mode=mode, print_scale_context=PrintScaleContext.from_frame(frame, {}),
        grouping={"max_height_mm": 3, "width_mm": .84, "gap_mm": .4, "span_mm": 5},
    )
    assert mesh.bounds[1, 2] == pytest.approx(.4)
    assert records[0].get("group_id") == records[1].get("group_id") is not None
    for record in records:
        assert record["height_control"]["effective_tops_mm"] == pytest.approx([.4])
