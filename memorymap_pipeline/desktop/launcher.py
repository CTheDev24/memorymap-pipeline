from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Start the Qt desktop application; imports stay lazy for headless tooling."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:  # pragma: no cover - exercised without desktop extras
        raise RuntimeError(
            "The desktop app requires PySide6 and PySide6-WebEngine. "
            "Install the desktop dependency group before launching it."
        ) from exc

    from .window import MemoryMapWindow

    arguments = list(argv) if argv is not None else sys.argv
    application = QApplication.instance()
    owns_application = application is None
    if application is None:
        application = QApplication(arguments)
    application.setOrganizationName("MemoryMap")
    application.setApplicationName("MemoryMap Pipeline")

    window = MemoryMapWindow()
    window.show()
    # Keep a reference when embedding into an existing QApplication.
    setattr(application, "_memorymap_window", window)
    return int(application.exec()) if owns_application else 0
