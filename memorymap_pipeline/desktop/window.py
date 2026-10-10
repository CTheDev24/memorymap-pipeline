"""Native Trace Studio window and bridge to the embedded map view.

Generation is deliberately exposed as a Qt signal so the application can wire the
existing in-process pipeline to a worker thread without running a localhost API.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from ..building_heights import BuildingHeightPolicy
from ..config import ROUTE_LAYER_HEIGHT_SLOPES, route_slope_for_layer_height
from ..gpx_loader import Route, load_route_from_gpx
from ..map_frame import MapFrame
from ..palettes import default_preset, resolve_palette
from .print_settings import ACCENTS, ROAD_CATEGORIES, finish_colors
from .project import STYLE_PROFILE_LANDSCAPE, STYLE_PROFILE_URBAN, DesktopProject
from .viewer_support import placeholder_html, viewer_url
from .worker import GenerationWorker

FRAME_ZOOM_FACTOR = 1.1

try:
    from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QColorDialog,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSplitter,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - depends on optional desktop extras
    if exc.name and exc.name.startswith("PySide6"):
        raise ImportError(
            "Trace Studio requires PySide6 (including the Qt WebEngine add-ons)"
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
        self.setWindowTitle("Trace Studio")
        self.menuBar().addAction("About", self._about)
        self.resize(1220, 780)
        self.gpx_path: Path | None = None
        self.route: Route | None = None
        self.result_path: Path | None = None
        self.preview_path: Path | None = None
        self.default_frame: dict | None = None
        self.current_frame: dict | None = None
        self.generation_thread: QThread | None = None
        self.generation_worker: GenerationWorker | None = None
        self.color_preset_name = default_preset(STYLE_PROFILE_URBAN)
        self.layer_colors = resolve_palette(STYLE_PROFILE_URBAN)
        self.color_buttons: dict[str, QPushButton] = {}
        self._height_revision = 0
        self._existing_appearance_settings = {}
        self.bridge = MapBridge(self)
        self.bridge.frame_changed.connect(self._remember_frame)
        self._build_ui()

    def _about(self) -> None:
        from ..build_identity import build_identity

        identity = build_identity()
        QMessageBox.information(
            self,
            "About Trace Studio",
            f"Trace Studio\n{identity['release']}\nSource: {identity['source_sha256']}",
        )

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        toolbar = QHBoxLayout()
        self.file_label = QLabel("No GPX selected")
        pick = QPushButton("Open GPX…")
        pick.clicked.connect(self.choose_gpx)
        toolbar.addWidget(pick)
        for label, callback in (
            ("Open project…", self.open_project),
            ("Save project…", self.save_project),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        toolbar.addWidget(self.file_label, 1)
        layout.addLayout(toolbar)

        split = QSplitter()
        self.view_tabs = QTabWidget()
        self.map_view = QWebEngineView()
        self.channel = QWebChannel(self.map_view.page())
        self.channel.registerObject("memoryMap", self.bridge)
        self.map_view.page().setWebChannel(self.channel)
        self.map_view.setHtml(self._map_html(), QUrl("https://localhost/"))
        self.preview_view = QWebEngineView()
        self.preview_view.loadFinished.connect(self._apply_palette_to_preview)
        self.preview_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
            True,
        )
        self.preview_view.setHtml(placeholder_html())
        self.view_tabs.addTab(self.map_view, "Map")
        self.preview_tab_index = self.view_tabs.addTab(self.preview_view, "3D Preview")
        split.addWidget(self.view_tabs)
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls_scroll.setMinimumWidth(380)
        controls_scroll.setWidget(self._controls())
        split.addWidget(controls_scroll)
        split.setSizes([820, 400])
        layout.addWidget(split, 1)
        self.setCentralWidget(root)

    def _controls(self) -> QWidget:
        panel = QWidget()
        outer = QVBoxLayout(panel)
        print_box = QGroupBox("Print frame")
        form = QFormLayout(print_box)
        orientation_row = QWidget()
        row = QHBoxLayout(orientation_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.landscape = QRadioButton("Landscape")
        self.portrait = QRadioButton("Portrait")
        self.landscape.setChecked(True)
        self.landscape.toggled.connect(self._orientation_changed)
        row.addWidget(self.landscape)
        row.addWidget(self.portrait)
        form.addRow("Orientation", orientation_row)
        self.print_width = self._spin(240, 10, 1000)
        self.print_height = self._spin(190, 10, 1000)
        self.margin = self._spin(5, 0, 100)
        self.route_clearance = self._spin(5, 0, 100)
        self.route_clearance.setToolTip(
            "Minimum buffer from the outside of the route to the model edge when fitting the course."
        )
        self.flat_border = QCheckBox("Flat trim around map")
        self.flat_border.setChecked(False)
        self.flat_border.setToolTip(
            "When enabled, reserves the configured width as a flat perimeter trim "
            "and clips every map layer inside it."
        )
        self.flat_border.toggled.connect(self._border_toggled)
        self.margin.setEnabled(False)
        self.route_width = self._spin(1.2, 0.1, 20)
        self.route_height = QComboBox()
        self.route_height.addItem("Auto (profile default)", None)
        for height in (1.0, 1.2, 1.5, 2.0, 2.5, 3.0):
            self.route_height.addItem(f"{height:.1f} mm", height)
        self.route_height.setToolTip(
            "Auto uses 1.2 mm for Landscape and 2.0 mm for Urban. "
            "Select a value to override the profile default."
        )
        self.route_layer_height = QComboBox()
        for layer_height, slope in ROUTE_LAYER_HEIGHT_SLOPES.items():
            self.route_layer_height.addItem(
                f"{layer_height:.2f} mm  ({slope:.0%} slope)", layer_height
            )
        self.route_layer_height.setCurrentIndex(self.route_layer_height.findData(0.16))
        self.route_layer_height.setToolTip(
            "Select the slicer layer height used for the route. Trace Studio chooses "
            "a matching maximum elevation slope to limit visible stair-stepping."
        )
        form.addRow("Width (mm)", self.print_width)
        form.addRow("Height (mm)", self.print_height)
        form.addRow("Border", self.flat_border)
        form.addRow("Route clearance (mm)", self.route_clearance)
        form.addRow("Trim width (mm)", self.margin)
        self.route_clearance.valueChanged.connect(
            lambda _value: self._border_toggled(self.flat_border.isChecked())
        )
        self.route_width.valueChanged.connect(
            lambda _value: self._border_toggled(self.flat_border.isChecked())
        )
        form.addRow("Route width (mm)", self.route_width)
        form.addRow("Route height", self.route_height)
        form.addRow("Route layer height", self.route_layer_height)
        marker_row = QWidget()
        marker_layout = QHBoxLayout(marker_row)
        marker_layout.setContentsMargins(0, 0, 0, 0)
        self.start_marker = QCheckBox("Start")
        self.finish_marker = QCheckBox("Finish")
        marker_layout.addWidget(self.start_marker)
        marker_layout.addWidget(self.finish_marker)
        form.addRow("Route markers", marker_row)
        reset = QPushButton("Reset frame to route")
        reset.clicked.connect(self.reset_frame)
        form.addRow(reset)
        zoom_row = QWidget()
        zoom_layout = QHBoxLayout(zoom_row)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_in = QPushButton("Zoom in")
        zoom_out = QPushButton("Zoom out")
        zoom_in.clicked.connect(self.zoom_frame_in)
        zoom_out.clicked.connect(self.zoom_frame_out)
        zoom_layout.addWidget(zoom_in)
        zoom_layout.addWidget(zoom_out)
        form.addRow("Route framing", zoom_row)
        outer.addWidget(print_box)

        style_box = QGroupBox("Map style")
        style_form = QFormLayout(style_box)
        self.style_profile = QComboBox()
        self.style_profile.addItem("Urban", STYLE_PROFILE_URBAN)
        self.style_profile.addItem("Landscape", STYLE_PROFILE_LANDSCAPE)
        self.style_profile.currentIndexChanged.connect(self._style_changed)
        self.surface_skin_thickness = self._spin(0.4, 0.2, 2.0)
        self.minimum_waterway_width = self._spin(0.8, 0.4, 5.0)
        self.surface_skin_thickness.setToolTip(
            "Visible green and blue surface layer thickness for landscape maps."
        )
        self.minimum_waterway_width.setToolTip(
            "Narrower mapped waterways are widened to this printable width."
        )
        self.ground_cover_mode = QComboBox()
        self.ground_cover_mode.addItem("Automatic (raster + OSM)", "auto")
        self.ground_cover_mode.addItem("OSM only", "osm-only")
        self.ground_cover_mode.addItem("Off", "off")
        self.ground_cover_sensitivity = QComboBox()
        self.ground_cover_sensitivity.addItem("Balanced", "balanced")
        self.ground_cover_sensitivity.addItem("Conservative", "conservative")
        self.ground_cover_sensitivity.addItem("Broad", "broad")
        self.ground_cover_mode.setToolTip(
            "Automatic downloads and caches WorldCover and annual land cover, then "
            "combines them with mapped beaches, sand, rock, cliffs, and water."
        )
        self.ground_cover_sensitivity.setToolTip(
            "Controls print-scale cleanup of exposed ground. Broad retains more "
            "small coastal patches; Conservative removes more marginal detail."
        )
        style_form.addRow("Profile", self.style_profile)
        style_form.addRow("Surface skin", self.surface_skin_thickness)
        style_form.addRow("Minimum waterway width", self.minimum_waterway_width)
        style_form.addRow("Ground cover", self.ground_cover_mode)
        style_form.addRow("Exposed-ground detail", self.ground_cover_sensitivity)
        outer.addWidget(style_box)

        palette_box = QGroupBox("Layer colors")
        palette_form = QFormLayout(palette_box)
        self.finish = QComboBox()
        for label in ("Gallery", "Nocturne"):
            self.finish.addItem(label, label)
        self.route_accent = QComboBox()
        for label in (*ACCENTS, "Neutral", "Color Wheel"):
            self.route_accent.addItem(label, label)
        self.custom_route_color = "#F7591F"
        self.previous_route_accent = "Signal Orange"
        self.finish.currentIndexChanged.connect(self._finish_changed)
        self.route_accent.activated.connect(self._route_accent_activated)
        palette_form.addRow("Finish", self.finish)
        palette_form.addRow("Route", self.route_accent)
        outer.addWidget(palette_box)
        self._finish_changed()

        layers = QGroupBox("Layers")
        layer_layout = QVBoxLayout(layers)
        self.route_layer = QCheckBox("Route")
        self.route_layer.setChecked(True)
        self.route_layer.toggled.connect(self.start_marker.setEnabled)
        self.route_layer.toggled.connect(self.finish_marker.setEnabled)
        self.roads_layer = QCheckBox("Roads")
        self.roads_layer.setChecked(True)
        self.buildings_layer = QCheckBox("Buildings")
        self.buildings_layer.setChecked(True)
        self.building_grouping = QCheckBox("Group small buildings (0.4 mm nozzle)")
        self.building_grouping.setToolTip(
            "Combine nearby low buildings into masses with a 0.8 mm footprint target. "
            "Omits alleys/driveways, uses 0.4 mm local streets and preserves route/water separation; "
            "some small buildings may remain individual."
        )
        self.building_grouping.toggled.connect(
            lambda enabled: self.roads_layer.setChecked(True) if enabled else None
        )
        self.buildings_layer.toggled.connect(self.building_grouping.setEnabled)
        self.terrain_layer = QCheckBox("Terrain (USGS 3DEP)")
        self.water_layer = QCheckBox("Water (gray, recessed)")
        self.water_mesh_body_hint = QCheckBox("Export as separate water mesh body")
        self.water_mesh_body_hint.setChecked(True)
        self.water_mesh_body_hint.setEnabled(False)
        self.water_layer.setEnabled(False)
        self.terrain_layer.toggled.connect(self._terrain_toggled)
        self.water_layer.toggled.connect(self._water_toggled)
        for control in (
            self.route_layer,
            self.roads_layer,
            self.buildings_layer,
            self.building_grouping,
            self.terrain_layer,
            self.water_layer,
            self.water_mesh_body_hint,
        ):
            layer_layout.addWidget(control)
        outer.addWidget(layers)

        road_box = QGroupBox("Road types")
        road_layout = QVBoxLayout(road_box)
        self.road_categories = {}
        for label in ROAD_CATEGORIES:
            control = QCheckBox(label)
            control.setChecked(label != "Footpaths / tracks")
            self.road_categories[label] = control
            road_layout.addWidget(control)
        road_box.setToolTip(
            "Choose exported roads. Public streets remain barriers when grouping buildings."
        )
        self.roads_layer.toggled.connect(road_box.setEnabled)
        outer.addWidget(road_box)
        building_box = QGroupBox("Building filter")
        building_form = QFormLayout(building_box)
        self.print_optimized = QCheckBox("Print-optimized buildings")
        self.print_optimized.setToolTip(
            "Simplify and group small buildings, omit isolated tiny features, and protect the route. "
            "Includes unclassified low-rise buildings; keeps landmarks and flags unresolved details."
        )
        self.building_line_width = self._spin(0.42, 0.2, 1.2)
        self.building_line_width.setEnabled(False)
        building_form.addRow(self.print_optimized)
        building_form.addRow("Printer line width (mm)", self.building_line_width)
        self.filter_residential = QCheckBox("Omit narrow residential buildings")
        self.residential_width = self._spin(0.8, 0.1, 5.0)
        self.residential_width.setEnabled(False)
        self.filter_residential.toggled.connect(self.residential_width.setEnabled)
        building_box.setToolTip(
            "Applied after grouping at the final map scale. Removes identified residential footprints with no core this wide. Unclassified buildings, landmarks, parts and courtyards are retained. This is geometric screening, not slicer certification."
        )
        building_form.addRow(self.filter_residential)
        building_form.addRow("Minimum footprint width", self.residential_width)
        self.buildings_layer.toggled.connect(building_box.setEnabled)
        self.print_optimized.toggled.connect(self._building_optimization_changed)
        self.buildings_layer.toggled.connect(self._building_optimization_changed)
        self.building_appearance = QComboBox()
        self.building_appearance.addItem("Existing", "existing")
        self.building_appearance.addItem("City relief", "city_relief")
        self.city_detail = QComboBox()
        for name in ("Sparse", "Balanced", "Dense"):
            self.city_detail.addItem(name, name.lower())
        self.city_detail.setCurrentIndex(1)
        self.city_landmarks = QCheckBox("Emphasize landmarks")
        self.city_landmarks.setChecked(True)
        self.city_detail.setEnabled(False)
        self.city_landmarks.setEnabled(False)
        building_form.addRow("Appearance", self.building_appearance)
        building_form.addRow("Neighborhood detail", self.city_detail)
        building_form.addRow(self.city_landmarks)
        self.city_guidance = QLabel(
            "City relief uses illustrative heights and wider neighborhood buildings. Major roads and local streets near the route stay visible; quieter streets still constrain building placement."
        )
        self.city_guidance.setWordWrap(True)
        building_form.addRow(self.city_guidance)
        self.building_appearance.currentIndexChanged.connect(self._appearance_changed)
        self.building_line_width.valueChanged.connect(self._appearance_settings_changed)
        self.city_detail.currentIndexChanged.connect(self._appearance_settings_changed)
        self.city_landmarks.toggled.connect(self._appearance_settings_changed)
        outer.addWidget(building_box)

        terrain_box = QGroupBox("Terrain settings")
        terrain_form = QFormLayout(terrain_box)
        self.terrain_relief = self._spin(3.0, 0.5, 12.0)
        self.water_recess = self._spin(0.4, 0.1, 3.0)
        self.terrain_relief.setEnabled(False)
        self.water_recess.setEnabled(False)
        terrain_form.addRow("Maximum relief", self.terrain_relief)
        terrain_form.addRow("Water recess", self.water_recess)
        outer.addWidget(terrain_box)
        self.height_box = QGroupBox("Building heights")
        heights = QFormLayout(self.height_box)
        heights.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.building_height_mode = QComboBox()
        self.building_height_mode.addItem(
            "Architectural relief (current style)", "architectural_relief"
        )
        self.building_height_mode.addItem("Proportional to map scale", "proportional")
        self.building_height_multiplier = self._spin(1.0, 0.01, 20, decimals=2)
        self.building_height_multiplier.setDecimals(2)
        self.building_height_multiplier.setSuffix(" ×")
        self.building_height_min_enabled = QCheckBox("Minimum visible height")
        self.building_height_max_enabled = QCheckBox("Maximum visible height")
        self.building_height_min = self._spin(0.8, 0.01, 100, decimals=2)
        self.building_height_max = self._spin(30, 0.01, 100, decimals=2)
        for control in (self.building_height_min, self.building_height_max):
            control.setDecimals(2)
            control.setEnabled(False)
        self.building_max_height = self._spin(30.0, 9.0, 31.75)
        self.building_max_height.setToolTip(
            "Existing scale-adjusted relief ceiling, before multiplier and final limits."
        )
        heights.addRow("Height mode", self.building_height_mode)
        heights.addRow("Height multiplier", self.building_height_multiplier)
        heights.addRow(self.building_height_min_enabled, self.building_height_min)
        heights.addRow(self.building_height_max_enabled, self.building_height_max)
        heights.addRow("Relief ceiling (legacy)", self.building_max_height)
        note = QLabel(
            "Proportional 1× uses source heights at map scale. Missing heights use estimates. Limits include roofs and apply to whole building assemblies. Regenerate after edits."
        )
        note.setWordWrap(True)
        heights.addRow(note)
        reset = QPushButton("Reset building heights")
        reset.clicked.connect(self._reset_building_heights)
        heights.addRow(reset)
        self.building_height_mode.currentIndexChanged.connect(self._height_settings_changed)
        for control in (
            self.building_height_multiplier,
            self.building_height_min,
            self.building_height_max,
            self.building_max_height,
        ):
            control.valueChanged.connect(self._height_settings_changed)
        for control in (self.building_height_min_enabled, self.building_height_max_enabled):
            control.toggled.connect(self._height_settings_changed)
        self.buildings_layer.toggled.connect(self.height_box.setEnabled)
        outer.addWidget(self.height_box)
        self.generate = QPushButton("Generate 3MF")
        self.generate.setEnabled(False)
        self.generate.clicked.connect(self.request_generation)
        self.save = QPushButton("Save result as…")
        self.save.setEnabled(False)
        self.save.clicked.connect(self.save_result)
        outer.addWidget(self.generate)
        outer.addWidget(self.save)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.warnings = QTextEdit()
        self.warnings.setReadOnly(True)
        self.warnings.setMinimumHeight(140)
        self.warnings.setPlaceholderText("Generation warnings and status appear here.")
        outer.addWidget(self.progress)
        outer.addWidget(self.warnings, 1)
        self._style_changed()
        return panel

    @staticmethod
    def _spin(value: float, minimum: float, maximum: float, *, decimals: int = 1) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSuffix(" mm")
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
        if self.generation_thread is not None:
            self.add_warning("Wait for generation to finish before opening a GPX.")
            return
        name, _ = QFileDialog.getOpenFileName(self, "Choose GPX route", "", "GPX files (*.gpx)")
        if name:
            self.gpx_path = Path(name)
            self.route = load_route_from_gpx(self.gpx_path)
            self.file_label.setText(self.gpx_path.name)
            self.generate.setEnabled(True)
            frame = MapFrame.fit_route(
                self.route.points,
                self.print_width.value(),
                self.print_height.value(),
                MemoryMapWindow._effective_margin(self),
                route_padding_mm=MemoryMapWindow._route_padding(self),
            )
            self.default_frame = {
                "center_lat": frame.center_lat,
                "center_lon": frame.center_lon,
                "coverage_width_m": frame.coverage_width_m,
                "coverage_height_m": frame.coverage_height_m,
                "rotation_degrees": frame.rotation_degrees,
                "print_width_mm": frame.print_width_mm,
                "print_height_mm": frame.print_height_mm,
                "margin_mm": frame.margin_mm,
            }
            self.current_frame = self.default_frame.copy()
            route_json = {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[p.longitude, p.latitude] for p in self.route.points],
                },
            }
            script = f"window.defaultFrame={json.dumps(self.default_frame)};setRoute({json.dumps(route_json)});setFrame(window.defaultFrame);"
            self.map_view.page().runJavaScript(script)

    @Slot(bool)
    def _orientation_changed(self, landscape: bool) -> None:
        is_landscape = self.print_width.value() >= self.print_height.value()
        if landscape == is_landscape:
            return
        width, height = self.print_height.value(), self.print_width.value()
        self.print_width.setValue(width)
        self.print_height.setValue(height)

        if self.route is not None:
            frame = MapFrame.fit_route(
                self.route.points,
                width,
                height,
                MemoryMapWindow._effective_margin(self),
                route_padding_mm=MemoryMapWindow._route_padding(self),
            )
            fitted = {
                "center_lat": frame.center_lat,
                "center_lon": frame.center_lon,
                "coverage_width_m": frame.coverage_width_m,
                "coverage_height_m": frame.coverage_height_m,
                "rotation_degrees": frame.rotation_degrees,
                "print_width_mm": frame.print_width_mm,
                "print_height_mm": frame.print_height_mm,
                "margin_mm": frame.margin_mm,
            }
            self.default_frame = fitted
            self.current_frame = fitted.copy()
        else:
            for frame in (self.default_frame, self.current_frame):
                if frame is None:
                    continue
                frame["print_width_mm"], frame["print_height_mm"] = width, height
                frame["coverage_width_m"], frame["coverage_height_m"] = (
                    frame["coverage_height_m"],
                    frame["coverage_width_m"],
                )

        if self.current_frame is not None:
            if self.default_frame is not None:
                self.map_view.page().runJavaScript(
                    f"window.defaultFrame={json.dumps(self.default_frame)};"
                )
            self.map_view.page().runJavaScript(f"setFrame({json.dumps(self.current_frame)});")

    @Slot(dict)
    def _remember_frame(self, frame: dict) -> None:
        if hasattr(self, "city_guidance"):
            meters_per_mm = float(frame["coverage_width_m"]) / float(frame["print_width_mm"])
            line = self.building_line_width.value() if hasattr(self, "building_line_width") else 0.4
            self.city_guidance.setText(
                f"Scale: {meters_per_mm:.1f} m/mm. A 10 m building is {10 / meters_per_mm:.2f} mm wide; two {line:.2f} mm lines need {2 * line * meters_per_mm:.0f} m. City relief uses illustrative heights and wider neighborhood forms, with quieter roads."
            )
        self.current_frame = frame.copy()

    @Slot()
    def reset_frame(self) -> None:
        if self.default_frame:
            self.current_frame = self.default_frame.copy()
            self.map_view.page().runJavaScript("resetFrame()")

    def _zoom_frame(self, factor: float) -> None:
        if self.current_frame is None:
            return
        self.current_frame = self.current_frame.copy()
        self.current_frame["coverage_width_m"] *= factor
        self.current_frame["coverage_height_m"] *= factor
        self.map_view.page().runJavaScript(f"setFrame({json.dumps(self.current_frame)});")

    @Slot()
    def zoom_frame_in(self) -> None:
        self._zoom_frame(1.0 / FRAME_ZOOM_FACTOR)

    @Slot()
    def zoom_frame_out(self) -> None:
        self._zoom_frame(FRAME_ZOOM_FACTOR)

    def _route_padding(self) -> float:
        clearance = getattr(self, "route_clearance", None)
        width = getattr(self, "route_width", None)
        desired = float(clearance.value()) if clearance is not None else 5.0
        radius = float(width.value()) / 2 if width is not None else 0.6
        return max(0.0, desired - MemoryMapWindow._effective_margin(self)) + radius

    def _effective_margin(self) -> float:
        border = getattr(self, "flat_border", None)
        if border is not None and not border.isChecked():
            return 0.0
        return float(self.margin.value())

    @Slot(bool)
    def _border_toggled(self, enabled: bool) -> None:
        self.margin.setEnabled(enabled)
        if self.route is None:
            return
        frame = MapFrame.fit_route(
            self.route.points,
            self.print_width.value(),
            self.print_height.value(),
            MemoryMapWindow._effective_margin(self),
            route_padding_mm=MemoryMapWindow._route_padding(self),
        )
        fitted = {
            "center_lat": frame.center_lat,
            "center_lon": frame.center_lon,
            "coverage_width_m": frame.coverage_width_m,
            "coverage_height_m": frame.coverage_height_m,
            "rotation_degrees": frame.rotation_degrees,
            "print_width_mm": frame.print_width_mm,
            "print_height_mm": frame.print_height_mm,
            "margin_mm": frame.margin_mm,
        }
        self.default_frame = fitted
        self.current_frame = fitted.copy()
        self.map_view.page().runJavaScript(
            f"window.defaultFrame={json.dumps(self.default_frame)};"
            f"setFrame({json.dumps(self.current_frame)});"
        )

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

    @Slot(int)
    def _style_changed(self, _index: int = -1) -> None:
        landscape = self.style_profile.currentData() == STYLE_PROFILE_LANDSCAPE
        self.surface_skin_thickness.setEnabled(landscape)
        self.minimum_waterway_width.setEnabled(landscape)
        self.ground_cover_mode.setEnabled(landscape)
        self.ground_cover_sensitivity.setEnabled(landscape)

    @Slot(int)
    def _finish_changed(self, _index: int = -1) -> None:
        self.color_preset_name = "urban-classic"
        self.layer_colors = finish_colors(
            self.finish.currentData(), self.route_accent.currentData(), self.custom_route_color
        )
        self._apply_palette_to_preview()
        if self.result_path is not None and hasattr(self, "save"):
            self.save.setEnabled(False)
            self.add_warning("Regenerate the map to save the changed finish or route color.")

    @Slot(int)
    def _route_accent_activated(self, _index: int = -1) -> None:
        if self.route_accent.currentData() == "Color Wheel":
            from PySide6.QtGui import QColor

            selected = QColorDialog.getColor(
                QColor(self.custom_route_color),
                self,
                "Choose route color",
                QColorDialog.ColorDialogOption.DontUseNativeDialog,
            )
            if not selected.isValid():
                self.route_accent.setCurrentIndex(
                    self.route_accent.findData(self.previous_route_accent)
                )
                return
            self.custom_route_color = selected.name().upper()
        self.previous_route_accent = self.route_accent.currentData()
        self._finish_changed()

    @Slot(bool)
    def _apply_palette_to_preview(self, _loaded: bool = True) -> None:
        if not hasattr(self, "preview_view"):
            return
        script = f"window.setLayerColors && window.setLayerColors({json.dumps(self.layer_colors)});"
        self.preview_view.page().runJavaScript(script)

    def _building_optimization_changed(self, _checked: bool = False) -> None:
        optimized = self.print_optimized.isChecked()
        enabled = self.buildings_layer.isChecked()
        self.building_grouping.setEnabled(enabled and not optimized)
        self.filter_residential.setEnabled(enabled and not optimized)
        self.residential_width.setEnabled(
            enabled and not optimized and self.filter_residential.isChecked()
        )
        self.building_line_width.setEnabled(enabled and optimized)
        if optimized and enabled:
            self.roads_layer.setChecked(True)

    def _appearance_settings_changed(self, *_args) -> None:
        self._height_revision += 1
        self.save.setEnabled(False)
        if self.result_path is not None:
            self.preview_view.setHtml(
                placeholder_html("Appearance changed. Regenerate to update the model.")
            )

    def _appearance_changed(self, *_args) -> None:
        city = self.building_appearance.currentData() == "city_relief"
        if city:
            self._existing_appearance_settings = {
                "roads_enabled": self.roads_layer.isChecked(),
                "optimized": self.print_optimized.isChecked(),
                "height_mode": self.building_height_mode.currentData(),
                "roads": {label: c.isChecked() for label, c in self.road_categories.items()},
            }
            self.print_optimized.setChecked(True)
            self.building_height_mode.setCurrentIndex(0)
            for label, c in self.road_categories.items():
                if any(t in {"service", "cycleway"} for t in ROAD_CATEGORIES[label]):
                    c.setChecked(False)
        elif self._existing_appearance_settings:
            old = self._existing_appearance_settings
            self.print_optimized.setChecked(old["optimized"])
            self.building_height_mode.setCurrentIndex(
                self.building_height_mode.findData(old["height_mode"])
            )
            for label, c in self.road_categories.items():
                c.setChecked(old["roads"].get(label, c.isChecked()))
            self.roads_layer.setChecked(old.get("roads_enabled", True))
        self.print_optimized.setEnabled(not city)
        self.building_height_mode.setEnabled(not city)
        self.city_detail.setEnabled(city)
        self.city_landmarks.setEnabled(city)
        self._appearance_settings_changed()

    def _height_config(self) -> dict:
        if not hasattr(self, "building_height_mode"):
            return BuildingHeightPolicy().config()
        return BuildingHeightPolicy(
            self.building_height_mode.currentData(),
            self.building_height_multiplier.value(),
            self.building_height_min.value()
            if self.building_height_min_enabled.isChecked()
            else None,
            self.building_height_max.value()
            if self.building_height_max_enabled.isChecked()
            else None,
        ).config()

    def _height_settings_changed(self, *_args) -> None:
        self._height_revision += 1
        self.building_height_min.setEnabled(self.building_height_min_enabled.isChecked())
        self.building_height_max.setEnabled(self.building_height_max_enabled.isChecked())
        self.building_max_height.setEnabled(
            self.building_height_mode.currentData() == "architectural_relief"
        )
        if self.result_path is not None:
            self.save.setEnabled(False)
            self.preview_view.setHtml(
                placeholder_html(
                    "Building heights changed. Regenerate to update the preview and exported model."
                )
            )

    def _reset_building_heights(self) -> None:
        self.building_height_mode.setCurrentIndex(0)
        self.building_height_multiplier.setValue(1)
        self.building_height_min_enabled.setChecked(False)
        self.building_height_max_enabled.setChecked(False)
        self.building_max_height.setValue(30)

    def _project_state(self) -> DesktopProject:
        if self.current_frame is None or self.gpx_path is None:
            raise ValueError("Open a GPX before saving a project")
        config = self._generation_config_payload()
        frame = dict(self.current_frame)
        frame.update(
            print_width_mm=self.print_width.value(),
            print_height_mm=self.print_height.value(),
            margin_mm=MemoryMapWindow._effective_margin(self),
        )
        return DesktopProject(
            frame=MapFrame(**frame),
            gpx_path=str(self.gpx_path.resolve()),
            include_roads=self.roads_layer.isChecked(),
            include_buildings=self.buildings_layer.isChecked(),
            include_terrain=self.terrain_layer.isChecked(),
            include_water=self.water_layer.isChecked(),
            building_grouping_enabled=self.building_grouping.isChecked(),
            building_generalization_mode=config["building_generalization_mode"],
            building_appearance_mode=self.building_appearance.currentData(),
            city_relief_detail=self.city_detail.currentData(),
            city_relief_landmark_emphasis=self.city_landmarks.isChecked(),
            existing_appearance_settings=dict(self._existing_appearance_settings),
            building_line_width_mm=self.building_line_width.value(),
            **MemoryMapWindow._height_config(self),
            max_print_height_mm=self.building_max_height.value(),
            flat_border_enabled=self.flat_border.isChecked(),
            terrain_relief_mm=self.terrain_relief.value(),
            water_recess_mm=self.water_recess.value(),
            route_width_mm=self.route_width.value(),
            route_height_mm=self.route_height.currentData(),
            route_markers=config["route_markers"],
            route_layer_height_mm=self.route_layer_height.currentData(),
            style_profile=self.style_profile.currentData(),
            surface_skin_thickness_mm=self.surface_skin_thickness.value(),
            minimum_waterway_width_mm=self.minimum_waterway_width.value(),
            ground_cover_mode=self.ground_cover_mode.currentData(),
            ground_cover_sensitivity=self.ground_cover_sensitivity.currentData(),
            color_preset=self.color_preset_name,
            layer_colors=dict(self.layer_colors),
            extra_generation_settings={
                "road_types": config.get("road_types", []),
                "residential_min_width_mm": self.residential_width.value()
                if self.filter_residential.isChecked()
                else 0,
                "include_route": self.route_layer.isChecked(),
                "finish": self.finish.currentData(),
                "route_accent": self.route_accent.currentData(),
                "custom_route_color": self.custom_route_color,
                "route_clearance_mm": self.route_clearance.value(),
            },
        )

    def save_project(self) -> None:
        try:
            project = self._project_state()
            name, _ = QFileDialog.getSaveFileName(
                self, "Save Trace project", "map.trace.json", "Trace project (*.json)"
            )
            if name:
                project.save(name)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Cannot save project", str(exc))

    def open_project(self) -> None:
        if self.generation_thread is not None:
            self.add_warning("Wait for generation to finish before opening a project.")
            return
        name, _ = QFileDialog.getOpenFileName(
            self, "Open Trace project", "", "Trace project (*.json)"
        )
        if not name:
            return
        try:
            project = DesktopProject.load(name)
            if not project.gpx_path:
                raise ValueError("Project has no GPX path")
            route_path = Path(project.gpx_path)
            if not route_path.is_absolute():
                route_path = Path(name).parent / route_path
            route = load_route_from_gpx(route_path)
            self._apply_project(project, route_path, route)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Cannot open project", str(exc))

    def _apply_project(self, project, route_path, route) -> None:
        self.gpx_path, self.route = route_path, route
        self.file_label.setText(route_path.name)
        extra = project.extra_generation_settings
        checks = {
            "city_landmarks": project.city_relief_landmark_emphasis,
            "roads_layer": project.include_roads,
            "buildings_layer": project.include_buildings,
            "terrain_layer": project.include_terrain,
            "water_layer": project.include_water,
            "flat_border": project.flat_border_enabled,
            "building_grouping": project.building_grouping_enabled,
            "print_optimized": project.building_generalization_mode == "print_optimized",
            "building_height_min_enabled": project.building_height_min_mm is not None,
            "building_height_max_enabled": project.building_height_max_mm is not None,
            "start_marker": project.route_markers in {"start", "both"},
            "finish_marker": project.route_markers in {"finish", "both"},
            "filter_residential": extra.get("residential_min_width_mm", 0) > 0,
            "route_layer": extra.get("include_route", True),
        }
        values = {
            "print_width": project.frame.print_width_mm,
            "print_height": project.frame.print_height_mm,
            "margin": project.frame.margin_mm,
            "terrain_relief": project.terrain_relief_mm,
            "water_recess": project.water_recess_mm,
            "route_width": project.route_width_mm,
            "building_line_width": project.building_line_width_mm,
            "building_max_height": project.max_print_height_mm,
            "building_height_multiplier": project.building_height_multiplier,
            "building_height_min": project.building_height_min_mm or 0.8,
            "building_height_max": project.building_height_max_mm or 30,
            "surface_skin_thickness": project.surface_skin_thickness_mm,
            "minimum_waterway_width": project.minimum_waterway_width_mm,
            "residential_width": extra.get("residential_min_width_mm", 0.8) or 0.8,
            "route_clearance": extra.get("route_clearance_mm", 5),
        }
        combos = {
            "building_appearance": project.building_appearance_mode,
            "city_detail": project.city_relief_detail,
            "style_profile": project.style_profile,
            "route_height": project.route_height_mm,
            "route_layer_height": project.route_layer_height_mm,
            "building_height_mode": project.building_height_mode,
            "ground_cover_mode": project.ground_cover_mode,
            "ground_cover_sensitivity": project.ground_cover_sensitivity,
            "finish": extra.get("finish", self.finish.currentData()),
            "route_accent": extra.get("route_accent", self.route_accent.currentData()),
        }
        # Restore an immutable snapshot without intermediate refits or color updates.
        for name, value in checks.items():
            control = getattr(self, name)
            control.blockSignals(True)
            control.setChecked(value)
            control.blockSignals(False)
        for name, value in values.items():
            control = getattr(self, name)
            control.blockSignals(True)
            control.setRange(min(control.minimum(), value), max(control.maximum(), value))
            control.setValue(value)
            control.blockSignals(False)
        for name, value in combos.items():
            control = getattr(self, name)
            control.blockSignals(True)
            index = control.findData(value)
            if index < 0:
                control.addItem(str(value), value)
                index = control.count() - 1
            control.setCurrentIndex(index)
            control.blockSignals(False)
        if "road_types" in extra:
            for label, control in self.road_categories.items():
                control.setChecked(any(t in extra["road_types"] for t in ROAD_CATEGORIES[label]))
        self.custom_route_color = extra.get("custom_route_color", self.custom_route_color)
        self.previous_route_accent = self.route_accent.currentData()
        self.color_preset_name = project.color_preset or default_preset(project.style_profile)
        self.layer_colors = resolve_palette(
            project.style_profile, self.color_preset_name, project.layer_colors
        )
        self.landscape.blockSignals(True)
        self.portrait.blockSignals(True)
        self.landscape.setChecked(project.frame.print_width_mm >= project.frame.print_height_mm)
        self.portrait.setChecked(project.frame.print_width_mm < project.frame.print_height_mm)
        self.landscape.blockSignals(False)
        self.portrait.blockSignals(False)
        self.current_frame = asdict(project.frame)
        self.default_frame = self.current_frame.copy()
        self._existing_appearance_settings = dict(project.existing_appearance_settings)
        city = project.building_appearance_mode == "city_relief"
        self.print_optimized.setEnabled(not city)
        self.building_height_mode.setEnabled(not city)
        self.city_detail.setEnabled(city)
        self.city_landmarks.setEnabled(city)
        self._height_settings_changed()
        self._terrain_toggled(project.include_terrain)
        self._building_optimization_changed()
        self._style_changed()
        self.margin.setEnabled(project.flat_border_enabled)
        self.height_box.setEnabled(project.include_buildings)
        self.generate.setEnabled(True)
        self.save.setEnabled(False)
        self.result_path = self.preview_path = None
        self.preview_view.setHtml(
            placeholder_html("Project loaded. Generate to create a matching preview and model.")
        )
        route_json = {
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "LineString",
                "coordinates": [[p.longitude, p.latitude] for p in route.points],
            },
        }
        self.map_view.page().runJavaScript(
            f"window.defaultFrame={json.dumps(self.default_frame)};setRoute({json.dumps(route_json)});setFrame(window.defaultFrame);"
        )

    def _generation_config_payload(self) -> dict:
        route_layer_height = float(self.route_layer_height.currentData())
        start_marker = bool(getattr(self, "start_marker", None) and self.start_marker.isChecked())
        finish_marker = bool(
            getattr(self, "finish_marker", None) and self.finish_marker.isChecked()
        )
        marker_mode = (
            "both"
            if start_marker and finish_marker
            else "start"
            if start_marker
            else "finish"
            if finish_marker
            else "none"
        )
        selected_roads = [
            t
            for label, control in getattr(self, "road_categories", {}).items()
            if control.isChecked()
            for t in ROAD_CATEGORIES[label]
        ]
        optimized = bool(
            getattr(self, "print_optimized", None) and self.print_optimized.isChecked()
        )
        return {
            "building_appearance_mode": self.building_appearance.currentData()
            if hasattr(self, "building_appearance")
            else "existing",
            "city_relief_detail": self.city_detail.currentData()
            if hasattr(self, "city_detail")
            else "balanced",
            "city_relief_landmark_emphasis": self.city_landmarks.isChecked()
            if hasattr(self, "city_landmarks")
            else True,
            "building_generalization_mode": "print_optimized" if optimized else "manual",
            **({"line_width_mm": self.building_line_width.value()} if optimized else {}),
            **(
                {"road_types": selected_roads, "landscape_road_types": selected_roads}
                if hasattr(self, "road_categories")
                else {}
            ),
            "residential_min_width_mm": (
                self.residential_width.value()
                if not optimized
                and getattr(self, "filter_residential", None)
                and self.filter_residential.isChecked()
                else 0.0
            ),
            "building_grouping_enabled": bool(
                getattr(self, "building_grouping", None) and self.building_grouping.isChecked()
            ),
            "terrain_enabled": self.terrain_layer.isChecked(),
            "water_enabled": self.water_layer.isChecked(),
            "terrain_max_relief_mm": self.terrain_relief.value(),
            "water_recess_mm": self.water_recess.value(),
            "max_print_height_mm": self.building_max_height.value(),
            **MemoryMapWindow._height_config(self),
            "route_layer_height_mm": route_layer_height,
            "route_profile_max_slope": route_slope_for_layer_height(route_layer_height),
            "route_markers": marker_mode,
            "style_profile": self.style_profile.currentData(),
            "surface_skin_thickness_mm": self.surface_skin_thickness.value(),
            "minimum_waterway_width_mm": self.minimum_waterway_width.value(),
            "ground_cover_mode": self.ground_cover_mode.currentData(),
            "ground_cover_sensitivity": self.ground_cover_sensitivity.currentData(),
            "color_preset": getattr(
                self, "color_preset_name", default_preset(self.style_profile.currentData())
            ),
            "layer_colors": dict(
                getattr(
                    self,
                    "layer_colors",
                    resolve_palette(self.style_profile.currentData()),
                )
            ),
            "flat_border_enabled": bool(
                getattr(self, "flat_border", None) and self.flat_border.isChecked()
            ),
        }

    @Slot()
    def request_generation(self) -> None:
        if not self.gpx_path or not self.route or not self.current_frame:
            return
        self._generation_height_revision = self._height_revision
        self.height_box.setEnabled(False)
        self.save.setEnabled(False)
        self.progress.setValue(0)
        self.warnings.clear()
        self.generate.setEnabled(False)
        self.generate.setText("Generating…")
        self.finish.setEnabled(False)
        self.route_accent.setEnabled(False)
        try:
            frame_data = dict(self.current_frame)
            frame_data.update(
                print_width_mm=self.print_width.value(),
                print_height_mm=self.print_height.value(),
                margin_mm=MemoryMapWindow._effective_margin(self),
            )
            directory = Path(tempfile.mkdtemp(prefix="memorymap-desktop-"))
            payload = {
                "route": self.route,
                "frame": MapFrame(**frame_data),
                "output_path": directory / "memorymap.3mf",
                "include_base": True,
                "include_route": self.route_layer.isChecked(),
                "include_roads": self.roads_layer.isChecked(),
                "include_buildings": self.buildings_layer.isChecked(),
                "route_width_mm": self.route_width.value(),
                "route_height_mm": self.route_height.currentData(),
                "config": self._generation_config_payload(),
            }
            thread = QThread(self)
            worker = GenerationWorker(payload)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.progress.connect(self.set_progress)
            worker.warning.connect(self.add_warning)
            worker.completed.connect(self.set_result)
            worker.failed.connect(self._generation_failed)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._generation_finished)
            self.generation_thread, self.generation_worker = thread, worker
            thread.start()
        except Exception as exc:
            self._generation_failed(str(exc))
            self._generation_finished()

    @Slot(str)
    def _generation_failed(self, message: str) -> None:
        self.generate.setText("Retry generation")
        self.save.setEnabled(False)
        self.preview_view.setHtml(
            placeholder_html(
                "Generation failed. The previous successful file is preserved; retry to create a new matching result."
            )
        )
        QMessageBox.critical(self, "Generation failed", message)
        self.add_warning(message)

    @Slot()
    def _generation_finished(self) -> None:
        self.generate.setEnabled(self.gpx_path is not None)
        for name in ("finish", "route_accent"):
            control = getattr(self, name, None)
            if control is not None:
                control.setEnabled(True)
        if hasattr(self, "height_box"):
            self.height_box.setEnabled(self.buildings_layer.isChecked())
        self.generation_thread = None
        self.generation_worker = None

    @Slot(int, str)
    def set_progress(self, value: int, message: str = "") -> None:
        self.progress.setValue(max(0, min(100, value)))
        if message:
            self.warnings.append(message)

    @Slot(str, str)
    def set_result(self, path: str, preview_path: str = "") -> None:
        if getattr(self, "_generation_height_revision", 0) != getattr(self, "_height_revision", 0):
            self.save.setEnabled(False)
            self.add_warning("Height settings changed during generation. Regenerate before saving.")
            return
        self._result_height_revision = getattr(self, "_height_revision", 0)
        self.generate.setText("Regenerate map")
        self.result_path = Path(path)
        self.save.setEnabled(self.result_path.is_file())
        self.progress.setValue(100)
        self.preview_path = Path(preview_path) if preview_path else None
        if self.preview_path is None:
            self.preview_view.setHtml(
                placeholder_html(
                    "3D preview was unavailable, but the generated 3MF can still be saved."
                )
            )
            return
        try:
            self.preview_view.load(QUrl(viewer_url(self.preview_path)))
            self.view_tabs.setCurrentIndex(self.preview_tab_index)
        except (OSError, ValueError) as exc:
            self.preview_path = None
            self.preview_view.setHtml(
                placeholder_html(
                    "3D preview could not be opened. The generated 3MF is still available."
                )
            )
            self.add_warning(f"3D preview could not be opened: {exc}")

    @Slot(str)
    def add_warning(self, message: str) -> None:
        self.warnings.append(f"Warning: {message}")

    @Slot()
    def save_result(self) -> None:
        if getattr(self, "_result_height_revision", -1) != getattr(self, "_height_revision", 0):
            self.add_warning("Regenerate with the current height settings before saving.")
            return
        if not self.result_path or not self.result_path.is_file():
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "Save Trace Studio model", self.result_path.name, "3MF (*.3mf)"
        )
        if name:
            try:
                from .project import copy_generation_result

                copy_generation_result(self.result_path, Path(name))
            except OSError as exc:
                QMessageBox.critical(self, "Save failed", str(exc))


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MemoryMapWindow()
    window.show()
    return app.exec()


__all__ = ["MapBridge", "MemoryMapWindow", "run"]
