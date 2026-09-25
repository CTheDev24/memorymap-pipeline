"""Desktop application entry points and project-file helpers."""

from .project import DesktopProject

def run() -> int:
    """Launch the optional Qt UI without importing Qt during package discovery."""
    import sys
    if sys.argv[1:2] == ["--smoke-test"]:
        if len(sys.argv) != 3:
            return 2
        from .smoke_test import run as run_smoke_test
        return run_smoke_test(sys.argv[2])
    try:
        from .window import run as run_window
    except ImportError as exc:
        if exc.name and exc.name.startswith("PySide6"):
            raise RuntimeError(
                "Trace Studio requires PySide6 and PySide6-WebEngine"
            ) from exc
        raise
    return int(run_window())


__all__ = ["DesktopProject", "run"]
