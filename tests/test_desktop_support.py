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
