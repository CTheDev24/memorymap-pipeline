"""One explicit local job through the desktop's existing generation worker.

No listener, remote access, or control of another running desktop instance.
"""
from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path

from .project import DesktopProject


def load_job(request_path: str | Path):
    request_path = Path(request_path).resolve()
    data = json.loads(request_path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("Unsupported automation request version")
    unknown = set(data) - {"version", "project", "job_directory", "sources"}
    if unknown:
        raise ValueError(f"Unknown automation fields: {sorted(unknown)}")

    def resolve(value, base):
        path = Path(value)
        return (base / path).resolve() if not path.is_absolute() else path.resolve()

    project_path = resolve(data["project"], request_path.parent)
    project = DesktopProject.load(project_path)
    if not project.gpx_path:
        raise ValueError("Project has no GPX path")
    route_path = resolve(project.gpx_path, project_path.parent)
    if not route_path.is_file():
        raise ValueError(f"GPX does not exist: {route_path}")
    directory = resolve(data["job_directory"], request_path.parent)
    sources = data.get("sources", {})
    if not isinstance(sources, dict) or set(sources) - {"roads_file", "buildings_file", "water_file", "landcover_file"}:
        raise ValueError("Unsupported frozen source fields")
    sources = {key: resolve(value, request_path.parent) for key, value in sources.items()}
    for path in sources.values():
        if not path.is_file():
            raise ValueError(f"Source does not exist: {path}")
    # Never reuse an earlier job or overwrite owner files.
    directory.mkdir(parents=True, exist_ok=False)
    return project, route_path, directory, sources


def write_status(directory: Path, status: dict) -> None:
    temporary = directory / "status.tmp"
    temporary.write_text(json.dumps(status, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(directory / "status.json")


def verify_result(source: Path) -> dict:
    audit = json.loads(source.with_suffix(".audit.json").read_text(encoding="utf-8"))
    identity = sha256(source.read_bytes()).hexdigest()
    if audit.get("model_sha256") != identity:
        raise ValueError("Generated model does not match its audit")
    if any(value == "failed" for value in audit.get("layer_states", {}).values()):
        raise ValueError("Generated audit contains a failed layer")
    return audit


def run(request_path: str | Path) -> int:
    from PySide6.QtCore import QObject, Qt, QThread, QTimer, Slot
    from PySide6.QtWidgets import QApplication

    from ..gpx_loader import load_route_from_gpx
    from ..map_frame import MapFrame
    from .window import MemoryMapWindow
    from .worker import GenerationWorker

    project, route_path, directory, sources = load_job(request_path)
    status = {"version": 1, "state": "starting", "progress": 0, "warnings": []}
    write_status(directory, status)
    try:
        route = load_route_from_gpx(route_path)
        app = QApplication.instance() or QApplication([])
        app.setOrganizationName("Teklo Studio")
        app.setApplicationName("Trace Studio")

        class JobWindow(MemoryMapWindow):
            running = True

            def closeEvent(self, event):
                if self.running:
                    event.ignore()
                else:
                    super().closeEvent(event)

        window = JobWindow()
        window._apply_project(project, route_path, route)
        window.setWindowTitle("Trace Studio — local automation job")
        window.setWindowFlag(Qt.WindowCloseButtonHint, False)
        window.show()
        window.setEnabled(False)
        window._generation_height_revision = window._height_revision
        frame = dict(window.current_frame)
        frame.update(print_width_mm=window.print_width.value(),
                     print_height_mm=window.print_height.value(),
                     margin_mm=window._effective_margin())
        staging = directory / "staging"
        staging.mkdir()
        payload = {
            "map_style": window._explicit_map_style,
            "route": route, "frame": MapFrame(**frame),
            "output_path": staging / "map.3mf", "include_base": True,
            "include_route": window.route_layer.isChecked(),
            "include_roads": window.roads_layer.isChecked(),
            "include_buildings": window.buildings_layer.isChecked(),
            "route_width_mm": window.route_width.value(),
            "route_height_mm": window.route_height.currentData(),
            "config": window._generation_config_payload(), **sources,
        }
        window._project_state().save(directory / "effective.trace.json")
        status.update(state="running", settings=payload["config"])
        write_status(directory, status)

        class Controller(QObject):
            @Slot(int, str)
            def progress(self, value, message):
                window.set_progress(value, message)
                status.update(progress=value, message=message)
                write_status(directory, status)

            @Slot(str)
            def warning(self, message):
                window.add_warning(message)
                status["warnings"].append(message)
                write_status(directory, status)

            @Slot(str)
            def failed(self, message):
                status.update(state="failed", error=message)
                window.add_warning(message)
                write_status(directory, status)

            @Slot(str, str)
            def completed(self, model, preview):
                try:
                    source = Path(model)
                    audit = verify_result(source)
                    window.set_result(model, preview)
                    if (window.result_path != source
                            or window._result_height_revision != window._height_revision):
                        raise ValueError("Desktop rejected a stale generation result")
                    shutil.copy2(source, directory / "map.3mf")
                    shutil.copy2(source.with_suffix(".audit.json"), directory / "map.audit.json")
                    if preview:
                        shutil.copy2(preview, directory / "preview.glb")
                    status.update(state="complete", progress=100,
                                  model=str(directory / "map.3mf"),
                                  audit=str(directory / "map.audit.json"),
                                  model_sha256=audit["model_sha256"],
                                  layer_states=audit.get("layer_states", {}))
                    write_status(directory, status)
                except Exception as exc:  # noqa: BLE001 - Qt slot boundary must report failures
                    self.failed(str(exc))

            @Slot()
            def finished(self):
                if status["state"] not in {"complete", "failed"}:
                    self.failed("Worker finished without a result")
                window.running = False
                window.setEnabled(True)
                # This is a one-job session; wait for the thread before closing.
                app.exit(0 if status["state"] == "complete" else 1)

        controller = Controller(window)
        thread = QThread(window)
        worker = GenerationWorker(payload)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(controller.progress)
        worker.warning.connect(controller.warning)
        worker.completed.connect(controller.completed)
        worker.failed.connect(controller.failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(controller.finished)
        QTimer.singleShot(0, thread.start)
        result = app.exec()
        thread.wait()
        window.close()
        return int(result)
    except Exception as exc:  # noqa: BLE001 - job boundary must persist failure state
        status.update(state="failed", error=str(exc))
        write_status(directory, status)
        return 1
