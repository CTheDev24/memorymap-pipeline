"""Offline packaged-app smoke test: real Qt, generation, export and WebEngine."""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def run(report_path: str) -> int:
    destination = Path(report_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = destination.parent / (destination.stem + "-files")
    work.mkdir(exist_ok=True)
    report = {"passed": False, "stage": "Qt import", "frozen": bool(getattr(sys, "frozen", False))}

    def record(**values):
        report.update(values)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Only this explicit smoke mode changes platform/cache settings. No network
    # requests or user map files are needed for the synthetic generation.
    os.environ["QT_QPA_PLATFORM"] = "windows" if sys.platform == "win32" else "offscreen"
    os.environ["LOCALAPPDATA"] = str(work / "appdata")
    os.environ["MPLCONFIGDIR"] = str(work / "mpl-cache")
    record()
    try:
        from PySide6.QtCore import Qt, QTimer, qVersion
        from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineUrlRequestInterceptor
        from PySide6.QtWidgets import QApplication

        from .window import MemoryMapWindow

        app = QApplication.instance() or QApplication(["Trace Studio smoke test"])
        record(stage="window creation", qt_version=qVersion())
        if sys.platform == "win32":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetModuleHandleW.restype = ctypes.c_void_p
            kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
            buffer = ctypes.create_unicode_buffer(32768)
            kernel.GetModuleFileNameW(kernel.GetModuleHandleW("icuuc.dll"), buffer, len(buffer))
            record(loaded_icuuc=buffer.value)

        class OfflineOnly(QWebEngineUrlRequestInterceptor):
            def interceptRequest(self, info):
                if info.requestUrl().scheme() in {"http", "https"}:
                    info.block(True)

        interceptor = OfflineOnly(app)
        QWebEngineProfile.defaultProfile().setUrlRequestInterceptor(interceptor)
        window = MemoryMapWindow()
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        window.show()
        record(stage="offline generation", window_created=True)
        model, preview = _generate_fixture(work)
        record(stage="WebEngine preview", model_exported=str(model), preview_exported=str(preview))
        window.preview_view.loadFinished.connect(lambda ok: record(
            page_load_ok=ok, page_url=window.preview_view.url().toString()))
        window.preview_view.page().renderProcessTerminated.connect(lambda status, code: record(
            renderer_exit_status=str(status), renderer_exit_code=code))
        window._generation_height_revision = window._height_revision
        window.set_result(str(model), str(preview))
        timer = QTimer(app)
        timer.setInterval(250)

        def inspect(value):
            if not value or report.get("passed"):
                return
            data = json.loads(value)
            record(last_webengine_state=data)
            if data.get("ready"):
                record(passed=True, stage="complete", preview=data)
                app.exit(0)
            elif data.get("error"):
                record(stage="preview failed", error=data["status"])
                app.exit(1)

        def poll():
            window.preview_view.page().runJavaScript(
                "JSON.stringify({ready: !!document.getElementById('status')?.hidden, "
                "error: !!document.getElementById('status')?.classList.contains('error'), "
                "status: document.getElementById('status')?.textContent, "
                "info: document.getElementById('info')?.textContent, "
                "canvas: !!document.querySelector('canvas'), url: location.href, title: document.title})", inspect,
            )

        def timeout():
            if not report.get("passed"):
                record(stage="preview timed out", error="Preview did not load within 30 seconds")
                app.exit(1)

        timer.timeout.connect(poll)
        timer.start()
        QTimer.singleShot(30000, timeout)
        app.exec()
        timer.stop()
        window.close()
        return 0 if report["passed"] else 1
    except Exception:  # noqa: BLE001 - emit frozen startup errors to the report, not a dialog.
        record(error=traceback.format_exc())
        return 1


def _generate_fixture(work: Path) -> tuple[Path, Path]:
    import numpy as np

    from ..generation import GenerationRequest, generate_memory_map
    from ..gpx_loader import Route, RoutePoint
    from ..map_frame import MapFrame
    from .preview import export_preview_glb

    frame = MapFrame(29.76, -95.37, 1000, 1000, 100, 100)
    lat, lon = frame.print_to_lonlat(np.array([20, 80]), np.array([20, 20]))
    route = Route([RoutePoint(float(a), float(b)) for a, b in zip(lat, lon)])
    lat, lon = frame.print_to_lonlat(np.array([40, 60, 60, 40, 40]), np.array([40, 40, 60, 60, 40]))
    source = work / "building.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"building": "yes", "height": 10},
        "geometry": {"type": "Polygon", "coordinates": [list(zip(lon.tolist(), lat.tolist()))]},
    }]}), encoding="utf-8")
    result = generate_memory_map(GenerationRequest(
        route=route, frame=frame, output_path=work / "smoke.3mf", include_roads=False,
        buildings_file=source, config={"building_height_mode": "proportional", "building_height_multiplier": 2},
    ))
    if result.buildings_mesh is None or abs(result.buildings_mesh.bounds[1, 2] - 2) > 1e-6:
        raise ValueError("Packaged proportional-height generation produced the wrong roof height")
    preview = work / "smoke.glb"
    export_preview_glb(result.meshes, preview)
    return result.output_path, preview
