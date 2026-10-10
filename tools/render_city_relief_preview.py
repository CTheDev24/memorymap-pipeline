"""Depth-buffered, identical-camera renders using the real offline preview."""

import json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings
from memorymap_pipeline.desktop.viewer_support import viewer_url

path = Path(sys.argv[1]).resolve()
width = float(sys.argv[2]) if len(sys.argv) > 2 else 24
height = float(sys.argv[3]) if len(sys.argv) > 3 else 24
app = QApplication([])
view = QWebEngineView()
view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
view.resize(1000, 1000)
view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
view.show()
view.load(QUrl(viewer_url(path) + "&capture=1"))
views = iter(["top", "perspective", "side"])
ready = [False]


def capture(name):
    def save(value):
        import base64

        if not value or not value.startswith("data:image/png;base64,"):
            raise RuntimeError("WebGL capture failed")
        path.with_name(name + ".png").write_bytes(base64.b64decode(value.split(",", 1)[1]))
        print("saved", name, flush=True)
        next_view()

    view.page().runJavaScript("window.capturePreview()", save)


def next_view():
    name = next(views, None)
    if name is None:
        app.exit(0)
        return
    script = (
        "document.querySelectorAll('.panel,#help').forEach(e=>e.style.display='none');window.setLayerColors({base:'#dcd7cc',buildings:'#e5dfd2',roads:'#777d85',route:'#ee7027'});window.setPreviewCamera("
        + json.dumps(name)
        + ","
        + json.dumps([[0, 0, -1.6], [width, height, 16]])
        + ");"
    )
    view.page().runJavaScript(script, lambda _: QTimer.singleShot(800, lambda: capture(name)))


def inspect(value):
    if value and not ready[0]:
        ready[0] = True
        timer.stop()
        next_view()


def poll():
    view.page().runJavaScript("!!document.getElementById('status')?.hidden", inspect)


timer = QTimer()
timer.timeout.connect(poll)
timer.start(250)
QTimer.singleShot(30000, lambda: app.exit(1))
sys.exit(app.exec())
