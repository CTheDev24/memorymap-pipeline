"""Capture the actual Qt style controls without showing a window or using the network."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineUrlRequestInterceptor
from PySide6.QtWidgets import QApplication, QScrollArea

from memorymap_pipeline.desktop.window import MemoryMapWindow

output = Path(sys.argv[1]).resolve()
output.mkdir(parents=True, exist_ok=True)
app = QApplication([])


class Offline(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        if info.requestUrl().scheme() in {"http", "https"}:
            info.block(True)


interceptor = Offline(app)
QWebEngineProfile.defaultProfile().setUrlRequestInterceptor(interceptor)
window = MemoryMapWindow()
window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
window.resize(1450, 1050)
window.show()
styles = iter(("cityscape", "terrain", "street_grid"))


def capture_next():
    style = next(styles, None)
    if style is None:
        app.exit(0)
        return
    window.map_style.setCurrentIndex(window.map_style.findData(style))
    scroll = window.findChild(QScrollArea)
    scroll.verticalScrollBar().setValue(0)

    def capture():
        assert window.grab().save(str(output / f"{style}.png"))
        print("captured", style, flush=True)
        capture_next()

    QTimer.singleShot(500, capture)


QTimer.singleShot(800, capture_next)
QTimer.singleShot(15000, lambda: app.exit(1))
sys.exit(app.exec())
