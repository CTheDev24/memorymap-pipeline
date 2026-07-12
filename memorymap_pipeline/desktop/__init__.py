"""Desktop application entry points and project-file helpers."""

from .project import DesktopProject

def run() -> int:
    """Launch the optional Qt UI without importing Qt during package discovery."""
    try:
        from .window import run as run_window
    except ImportError as exc:
        if exc.name and exc.name.startswith("PySide6"):
            raise RuntimeError(
                "MemoryMap desktop requires PySide6 and PySide6-WebEngine"
            ) from exc
        raise
    return int(run_window())


__all__ = ["DesktopProject", "run"]
