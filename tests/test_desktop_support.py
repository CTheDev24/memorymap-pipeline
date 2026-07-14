from __future__ import annotations

import sys
import types

import pytest

from memorymap_pipeline.desktop.project import DesktopProject
from memorymap_pipeline.map_frame import MapFrame


def project() -> DesktopProject:
    return DesktopProject(frame=MapFrame(29.76, -95.37, 1200, 950, 240, 190, 8, 12),
                          gpx_path="sample.gpx", include_buildings=False, route_width_mm=1.5)


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
