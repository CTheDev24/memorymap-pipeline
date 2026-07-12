"""Native MemoryMap window and bridge to the embedded map view.

Generation is deliberately exposed as a Qt signal so the application can wire the
existing in-process pipeline to a worker thread without running a localhost API.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from ..gpx_loader import Route, load_route_from_gpx
from ..map_frame import MapFrame
from .worker import GenerationWorker

try:
    from PySide6.QtCore import QObject, QThread, QUrl, Signal, Slot
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout,
        QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar,
        QPushButton, QRadioButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
    )
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError as exc:  # pragma: no cover - depends on optional desktop extras
    if exc.name and exc.name.startswith("PySide6"):
        raise ImportError(
            "MemoryMap desktop requires PySide6 (including the Qt WebEngine add-ons)"
        ) from exc
    raise


class MapBridge(QObject):
    """Small JSON bridge usable by any future MapLibre HTML implementation."""

    frame_changed = Signal(dict)
    route_requested = Signal(str)
    reset_requested = Signal()

    @Slot(str)
    def updateFrame(self, value: str) -> None:  # noqa: N802 - JavaScript API
        try:
            frame = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return
        if isinstance(frame, dict):
            self.frame_changed.emit(frame)

    @Slot()
    def mapReady(self) -> None:  # noqa: N802 - JavaScript API
        """JavaScript readiness hook retained for the map implementation."""


class MemoryMapWindow(QMainWindow):
    generation_requested = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MemoryMap Studio")
        self.resize(1220, 780)
        self.gpx_path: Path | None = None
        self.route: Route | None = None
        self.result_path: Path | None = None
        self.default_frame: dict | None = None
        self.current_frame: dict | None = None
        self.generation_thread: QThread | None = None
        self.generation_worker: GenerationWorker | None = None
        self.bridge = MapBridge(self)
        self.bridge.frame_changed.connect(self._remember_frame)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        toolbar = QHBoxLayout()
        self.file_label = QLabel("No GPX selected")
        pick = QPushButton("Open GPX…")
        pick.clicked.connect(self.choose_gpx)
        toolbar.addWidget(pick)
        toolbar.addWidget(self.file_label, 1)
        layout.addLayout(toolbar)

        split = QSplitter()
        self.map_view = QWebEngineView()
        self.channel = QWebChannel(self.map_view.page())
        self.channel.registerObject("memoryMap", self.bridge)
        self.map_view.page().setWebChannel(self.channel)
        self.map_view.setHtml(self._map_html(), QUrl("https://localhost/"))
        split.addWidget(self.map_view)
        split.addWidget(self._controls())
        split.setSizes([880, 340])
        layout.addWidget(split, 1)
        self.setCentralWidget(root)

    def _controls(self) -> QWidget:
        panel = QWidget()
        outer = QVBoxLayout(panel)
        print_box = QGroupBox("Print frame")
        form = QFormLayout(print_box)
        orientation_row = QWidget()
        row = QHBoxLayout(orientation_row); row.setContentsMargins(0, 0, 0, 0)
        self.landscape = QRadioButton("Landscape"); self.portrait = QRadioButton("Portrait")
        self.landscape.setChecked(True)
        self.landscape.toggled.connect(self._orientation_changed)
        row.addWidget(self.landscape); row.addWidget(self.portrait)
        form.addRow("Orientation", orientation_row)
        self.print_width = self._spin(240, 10, 1000)
        self.print_height = self._spin(190, 10, 1000)
        self.margin = self._spin(8, 0, 100)
        self.route_width = self._spin(1.5, .1, 20)
        form.addRow("Width (mm)", self.print_width)
        form.addRow("Height (mm)", self.print_height)
        form.addRow("Margin (mm)", self.margin)
        form.addRow("Route width (mm)", self.route_width)
        reset = QPushButton("Reset frame to route")
        reset.clicked.connect(self.reset_frame)
        form.addRow(reset)
        outer.addWidget(print_box)

        layers = QGroupBox("Layers")
        layer_layout = QVBoxLayout(layers)
        self.route_layer = QCheckBox("Route"); self.route_layer.setChecked(True)
        self.roads_layer = QCheckBox("Roads"); self.roads_layer.setChecked(True)
        self.buildings_layer = QCheckBox("Buildings"); self.buildings_layer.setChecked(True)
        self.terrain_layer = QCheckBox("Terrain (USGS 3DEP)")
        self.water_layer = QCheckBox("Water (gray, recessed)")
        self.water_layer.setEnabled(False)
        self.terrain_layer.toggled.connect(self._terrain_toggled)
        self.water_layer.toggled.connect(self._water_toggled)
        for control in (
            self.route_layer,
            self.roads_layer,
            self.buildings_layer,
            self.terrain_layer,
            self.water_layer,
        ):
            layer_layout.addWidget(control)
        outer.addWidget(layers)

        terrain_box = QGroupBox("Terrain settings")
        terrain_form = QFormLayout(terrain_box)
        self.terrain_relief = self._spin(3.0, 0.5, 12.0)
        self.water_recess = self._spin(0.4, 0.1, 3.0)
        self.terrain_relief.setEnabled(False)
        self.water_recess.setEnabled(False)
        terrain_form.addRow("Maximum relief", self.terrain_relief)
        terrain_form.addRow("Water recess", self.water_recess)
        outer.addWidget(terrain_box)
        self.generate = QPushButton("Generate 3MF")
        self.generate.setEnabled(False)
        self.generate.clicked.connect(self.request_generation)
        self.save = QPushButton("Save result as…")
        self.save.setEnabled(False)
        self.save.clicked.connect(self.save_result)
        outer.addWidget(self.generate); outer.addWidget(self.save)
        self.progress = QProgressBar(); self.progress.setRange(0, 100)
        self.warnings = QTextEdit(); self.warnings.setReadOnly(True)
        self.warnings.setPlaceholderText("Generation warnings and status appear here.")
        outer.addWidget(self.progress); outer.addWidget(self.warnings, 1)
        return panel

    @staticmethod
    def _spin(value: float, minimum: float, maximum: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(); spin.setRange(minimum, maximum)
        spin.setDecimals(1); spin.setValue(value); spin.setSuffix(" mm")
        return spin

    @staticmethod
    def _map_html() -> str:
        return """<!doctype html><html><head><meta charset='utf-8'>
        <link rel='stylesheet' href='https://unpkg.com/maplibre-gl@5.6.1/dist/maplibre-gl.css'>
        <style>html,body,#map{height:100%;margin:0}.hint{position:absolute;z-index:2;top:12px;
        left:50%;transform:translateX(-50%);background:#fffffff0;padding:8px 12px;border-radius:7px;
        font:13px system-ui;box-shadow:0 2px 12px #0002}</style></head><body>
        <div class='hint'>Open a GPX file, then drag the blue print frame to reposition it.</div>
        <div id='map'></div><script src='qrc:///qtwebchannel/qwebchannel.js'></script>
        <script src='https://unpkg.com/maplibre-gl@5.6.1/dist/maplibre-gl.js'></script><script>
        let bridge,frame,dragStart;const M=111320;
        new QWebChannel(qt.webChannelTransport,c=>{bridge=c.objects.memoryMap;bridge.mapReady()});
        const map=new maplibregl.Map({container:'map',style:'https://demotiles.maplibre.org/style.json',center:[0,20],zoom:2});
        function ring(f){const lat=f.center_lat,lon=f.center_lon,dx=f.coverage_width_m/(2*M*Math.cos(lat*Math.PI/180)),dy=f.coverage_height_m/(2*M),a=f.rotation_degrees*Math.PI/180;return [[-dx,-dy],[dx,-dy],[dx,dy],[-dx,dy],[-dx,-dy]].map(p=>[lon+p[0]*Math.cos(a)-p[1]*Math.sin(a),lat+p[0]*Math.sin(a)+p[1]*Math.cos(a)])}
        function source(id,data,type){if(map.getSource(id))map.getSource(id).setData(data);else{map.addSource(id,{type:'geojson',data});map.addLayer(type==='line'?{id,source:id,type:'line',paint:{'line-color':'#ef553f','line-width':5}}:{id,source:id,type:'fill',paint:{'fill-color':'#2878d0','fill-opacity':.18,'fill-outline-color':'#1559a0'}})}}
        window.setRoute=function(route){source('route',route,'line');const c=route.geometry.coordinates;const b=c.reduce((x,p)=>[Math.min(x[0],p[0]),Math.min(x[1],p[1]),Math.max(x[2],p[0]),Math.max(x[3],p[1])],[Infinity,Infinity,-Infinity,-Infinity]);map.fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:70})};
        window.setFrame=function(value){frame=value;source('frame',{type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[ring(frame)]}},'fill')};
        window.resetFrame=function(){if(window.defaultFrame)setFrame(JSON.parse(JSON.stringify(window.defaultFrame)))};
        map.on('mousedown',e=>{if(map.getLayer('frame')&&map.queryRenderedFeatures(e.point,{layers:['frame']}).length){dragStart=e.lngLat;map.dragPan.disable()}});
        map.on('mousemove',e=>{if(!dragStart||!frame)return;frame.center_lon+=e.lngLat.lng-dragStart.lng;frame.center_lat+=e.lngLat.lat-dragStart.lat;dragStart=e.lngLat;setFrame(frame)});
        map.on('mouseup',()=>{if(dragStart&&bridge)bridge.updateFrame(JSON.stringify(frame));dragStart=null;map.dragPan.enable()});
        </script></body></html>"""

    @Slot()
    def choose_gpx(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Choose GPX route", "", "GPX files (*.gpx)")
        if name:
            self.gpx_path = Path(name)
            self.route = load_route_from_gpx(self.gpx_path)
            self.file_label.setText(self.gpx_path.name)
            self.generate.setEnabled(True)
            frame = MapFrame.fit_route(
                self.route.points, self.print_width.value(), self.print_height.value(), self.margin.value()
            )
            self.default_frame = {
                "center_lat": frame.center_lat, "center_lon": frame.center_lon,
                "coverage_width_m": frame.coverage_width_m,
                "coverage_height_m": frame.coverage_height_m,
                "rotation_degrees": frame.rotation_degrees,
                "print_width_mm": frame.print_width_mm, "print_height_mm": frame.print_height_mm,
                "margin_mm": frame.margin_mm,
            }
            self.current_frame = self.default_frame.copy()
            route_json = {"type": "Feature", "properties": {}, "geometry": {
                "type": "LineString", "coordinates": [[p.longitude, p.latitude] for p in self.route.points]}}
            script = f"window.defaultFrame={json.dumps(self.default_frame)};setRoute({json.dumps(route_json)});setFrame(window.defaultFrame);"
            self.map_view.page().runJavaScript(script)

    @Slot(bool)
    def _orientation_changed(self, landscape: bool) -> None:
        if not landscape:
            return
        if self.print_width.value() < self.print_height.value():
            self.print_width.setValue(self.print_height.value())
            self.print_height.setValue(190)

    @Slot(dict)
    def _remember_frame(self, frame: dict) -> None:
        self.current_frame = frame.copy()

    @Slot()
    def reset_frame(self) -> None:
        if self.default_frame:
            self.current_frame = self.default_frame.copy()
            self.map_view.page().runJavaScript("resetFrame()")

    @Slot(bool)
    def _terrain_toggled(self, enabled: bool) -> None:
        self.terrain_relief.setEnabled(enabled)
        self.water_layer.setEnabled(enabled)
        if not enabled:
            self.water_layer.setChecked(False)
        self.water_recess.setEnabled(enabled and self.water_layer.isChecked())

    @Slot(bool)
    def _water_toggled(self, enabled: bool) -> None:
        self.water_recess.setEnabled(enabled and self.terrain_layer.isChecked())

    @Slot()
    def request_generation(self) -> None:
        if not self.gpx_path or not self.route or not self.current_frame:
            return
        self.progress.setValue(0); self.warnings.clear(); self.generate.setEnabled(False)
        try:
            frame_data = dict(self.current_frame)
            frame_data.update(print_width_mm=self.print_width.value(), print_height_mm=self.print_height.value(), margin_mm=self.margin.value())
            directory = Path(tempfile.mkdtemp(prefix="memorymap-desktop-"))
            payload = {
                "route": self.route, "frame": MapFrame(**frame_data),
                "output_path": directory / "memorymap.3mf", "include_base": True,
                "include_route": self.route_layer.isChecked(),
                "include_roads": self.roads_layer.isChecked(),
                "include_buildings": self.buildings_layer.isChecked(),
                "route_width_mm": self.route_width.value(),
                "config": {
                    "terrain_enabled": self.terrain_layer.isChecked(),
                    "water_enabled": self.water_layer.isChecked(),
                    "terrain_max_relief_mm": self.terrain_relief.value(),
                    "water_recess_mm": self.water_recess.value(),
                },
            }
            thread = QThread(self); worker = GenerationWorker(payload)
            worker.moveToThread(thread); thread.started.connect(worker.run)
            worker.progress.connect(self.set_progress); worker.warning.connect(self.add_warning)
            worker.completed.connect(self.set_result); worker.failed.connect(self._generation_failed)
            worker.finished.connect(thread.quit); worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater); thread.finished.connect(self._generation_finished)
            self.generation_thread, self.generation_worker = thread, worker
            thread.start()
        except Exception as exc:
            self._generation_failed(str(exc)); self._generation_finished()

    @Slot(str)
    def _generation_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Generation failed", message); self.add_warning(message)

    @Slot()
    def _generation_finished(self) -> None:
        self.generate.setEnabled(self.gpx_path is not None)
        self.generation_thread = None; self.generation_worker = None

    @Slot(int, str)
    def set_progress(self, value: int, message: str = "") -> None:
        self.progress.setValue(max(0, min(100, value)))
        if message: self.warnings.append(message)

    @Slot(str)
    def set_result(self, path: str) -> None:
        self.result_path = Path(path); self.save.setEnabled(self.result_path.is_file())
        self.progress.setValue(100)

    @Slot(str)
    def add_warning(self, message: str) -> None:
        self.warnings.append(f"Warning: {message}")

    @Slot()
    def save_result(self) -> None:
        if not self.result_path or not self.result_path.is_file(): return
        name, _ = QFileDialog.getSaveFileName(self, "Save MemoryMap", self.result_path.name, "3MF (*.3mf)")
        if name:
            try: shutil.copy2(self.result_path, name)
            except OSError as exc: QMessageBox.critical(self, "Save failed", str(exc))


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MemoryMapWindow(); window.show()
    return app.exec()


__all__ = ["MapBridge", "MemoryMapWindow", "run"]
