from __future__ import annotations

import sys
import types

import pytest

from memorymap_pipeline.desktop.project import DesktopProject
from memorymap_pipeline.map_frame import MapFrame


def project() -> DesktopProject:
    return DesktopProject(frame=MapFrame(29.76, -95.37, 1200, 950, 240, 190, 8, 12),
                          gpx_path="sample.gpx", include_buildings=False, route_width_mm=1.5,
                          terrain_preset="rolling-terrain")


def test_project_json_and_file_round_trip(tmp_path):
    expected = project()
    assert DesktopProject.from_json(expected.to_json()) == expected
    path = tmp_path / "sample.memorymap.json"
    expected.save(path)
    assert DesktopProject.load(path) == expected


def test_project_rejects_unknown_version_and_invalid_dimensions():
    value = project().to_dict()
    value["version"] = 99
    with pytest.raises(ValueError, match="Unsupported"):
        DesktopProject.from_dict(value)
    value["version"] = 1
    value["route"]["width_mm"] = 0
    with pytest.raises(ValueError, match="positive"):
        DesktopProject.from_dict(value)


def test_launcher_initializes_and_shows_window(monkeypatch):
    events = []

    class Application:
        current = None
        @classmethod
        def instance(cls): return cls.current
        def __init__(self, argv): Application.current = self; events.append(("app", argv))
        def setOrganizationName(self, name): events.append(("organization", name))
        def setApplicationName(self, name): events.append(("application", name))
        def exec(self): events.append(("exec",)); return 7

    class Window:
        def show(self): events.append(("show",))

    qtwidgets = types.ModuleType("PySide6.QtWidgets")
    qtwidgets.QApplication = Application
    pyside = types.ModuleType("PySide6")
    window = types.ModuleType("memorymap_pipeline.desktop.window")
    window.MemoryMapWindow = Window
    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qtwidgets)
    monkeypatch.setitem(sys.modules, "memorymap_pipeline.desktop.window", window)
    from memorymap_pipeline.desktop.launcher import main
    assert main(["memorymap"]) == 7
    assert ("show",) in events
    assert events[-1] == ("exec",)

def test_orientation_toggle_swaps_dimensions_and_live_frame():
    pytest.importorskip("PySide6")
    from memorymap_pipeline.desktop.window import MemoryMapWindow
    from memorymap_pipeline.gpx_loader import Route, RoutePoint
    from memorymap_pipeline.map_frame import MapFrame

    class Spin:
        def __init__(self, value):
            self._value = value

        def value(self):
            return self._value

        def setValue(self, value):
            self._value = value

    scripts = []

    class Page:
        def runJavaScript(self, script):
            scripts.append(script)

    current = {
        "print_width_mm": 240.0,
        "print_height_mm": 190.0,
        "coverage_width_m": 2400.0,
        "coverage_height_m": 1900.0,
    }
    window = type("WindowState", (), {})()
    window.print_width = Spin(240.0)
    window.print_height = Spin(190.0)
    window.default_frame = current.copy()
    window.current_frame = current.copy()
    window.margin = Spin(5.0)
    window.route = Route([
        RoutePoint(latitude=36.1600, longitude=-86.7900),
        RoutePoint(latitude=36.1700, longitude=-86.7700),
    ])
    window.map_view = type("View", (), {"page": lambda self: Page()})()

    MemoryMapWindow._orientation_changed(window, False)

    assert (window.print_width.value(), window.print_height.value()) == (190.0, 240.0)
    frame = MapFrame(**window.current_frame)
    transformed = frame.transform_points(window.route.points)
    assert transformed[:, 0].min() >= 5.0 - 0.01
    assert transformed[:, 0].max() <= 185.0 + 0.01
    assert transformed[:, 1].min() >= 5.0 - 0.01
    assert transformed[:, 1].max() <= 235.0 + 0.01
    assert window.current_frame["margin_mm"] == 5.0
    assert scripts and "setFrame" in scripts[-1]


def test_frame_zoom_preserves_center_and_scales_coverage():
    pytest.importorskip("PySide6")
    from memorymap_pipeline.desktop.window import MemoryMapWindow

    scripts = []

    class Page:
        def runJavaScript(self, script):
            scripts.append(script)

    window = type("WindowState", (), {})()
    window.current_frame = {
        "center_lat": 29.76,
        "center_lon": -95.37,
        "coverage_width_m": 1_000.0,
        "coverage_height_m": 800.0,
    }
    window.map_view = type("View", (), {"page": lambda self: Page()})()

    MemoryMapWindow._zoom_frame(window, 1.1)

    assert window.current_frame["center_lat"] == pytest.approx(29.76)
    assert window.current_frame["center_lon"] == pytest.approx(-95.37)
    assert window.current_frame["coverage_width_m"] == pytest.approx(1_100.0)
    assert window.current_frame["coverage_height_m"] == pytest.approx(880.0)
    assert scripts and "setFrame" in scripts[-1]


def test_terrain_preset_populates_editable_desktop_starting_values():
    pytest.importorskip("PySide6")
    from memorymap_pipeline.desktop.window import MemoryMapWindow

    class Combo:
        def currentData(self):
            return "mountain-coast"

    class Spin:
        def __init__(self):
            self.value = None

        def setValue(self, value):
            self.value = value

    window = type("WindowState", (), {})()
    window.terrain_preset = Combo()
    window.terrain_relief = Spin()
    window.water_recess = Spin()
    MemoryMapWindow._terrain_preset_changed(window, 3)
    assert window.terrain_relief.value == pytest.approx(4.0)
    assert window.water_recess.value == pytest.approx(0.4)


def test_preflight_formatter_produces_concise_colored_summary():
    pytest.importorskip("PySide6")
    from memorymap_pipeline.desktop.window import format_preflight_report

    heading, details, color = format_preflight_report(
        {
            "status": "red",
            "issues": [
                {
                    "severity": "error",
                    "message": "route contains two disconnected printable sections",
                }
            ],
        }
    )

    assert heading == "RED preflight"
    assert "ERROR: route contains" in details
    assert color == "#c62828"
