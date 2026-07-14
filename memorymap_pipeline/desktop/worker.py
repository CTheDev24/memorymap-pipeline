"""Qt worker for running the in-process generation service off the UI thread."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class GenerationWorker(QObject):
    progress = Signal(int, str)
    warning = Signal(str)
    completed = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, request_data: dict) -> None:
        super().__init__()
        self.request_data = request_data

    @Slot()
    def run(self) -> None:
        try:
            from ..generation import GenerationRequest, generate_memory_map

            request = GenerationRequest(**self.request_data)
            result = generate_memory_map(request, progress_callback=self._progress)
            for warning in result.warnings:
                self.warning.emit(str(warning))
            self.completed.emit(str(result.output_path))
        except Exception as exc:  # worker boundary: surface all pipeline failures
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    def _progress(self, value: int | float, message: str = "") -> None:
        self.progress.emit(max(0, min(100, int(value))), str(message))


__all__ = ["GenerationWorker"]
