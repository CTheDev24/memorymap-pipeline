from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable


def default_source_cache_dir() -> Path:
    """Return a writable per-user cache directory in source and frozen builds."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "MemoryMap" / "source-cache"
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "memorymap" / "source-cache"


def stable_request_hash(source: str, request: object) -> str:
    canonical = json.dumps(
        {"source": source, "request": request},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceProvenance:
    source: str
    endpoint: str
    request_hash: str
    cache_status: str
    retrieved_at: str | None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


class SourceCache:
    """Small, bounded byte cache with atomic writes and stale-on-error support."""

    def __init__(
        self,
        directory: str | Path | None = None,
        ttl_seconds: float = 7 * 24 * 60 * 60,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        if ttl_seconds < 0 or max_bytes < 1:
            raise ValueError("Source cache TTL and size must be non-negative")
        self.directory = Path(directory) if directory is not None else default_source_cache_dir()
        self.ttl_seconds = float(ttl_seconds)
        self.max_bytes = int(max_bytes)

    def fetch(
        self,
        *,
        source: str,
        endpoint: str,
        request: object,
        fetcher: Callable[[], bytes],
        validator: Callable[[bytes], bool] | None = None,
    ) -> tuple[bytes, SourceProvenance]:
        key = stable_request_hash(source, request)
        payload_path = self.directory / f"{key}.bin"
        metadata_path = self.directory / f"{key}.json"
        cached = self._read(payload_path, metadata_path, validator)
        now = time.time()
        if cached is not None:
            payload, metadata = cached
            age = max(0.0, now - float(metadata.get("stored_epoch", 0.0)))
            if age <= self.ttl_seconds:
                return payload, self._provenance(
                    source, endpoint, key, "cache_hit", metadata.get("retrieved_at")
                )

        try:
            payload = fetcher()
        except Exception:
            if cached is not None:
                payload, metadata = cached
                return payload, self._provenance(
                    source, endpoint, key, "stale_cache", metadata.get("retrieved_at")
                )
            raise

        # Invalid successful responses are source/data errors, not transient network
        # failures. Never cache them and never silently substitute stale data for them.
        if not isinstance(payload, bytes) or not payload:
            raise ValueError(f"{source} returned an empty response")
        if validator is not None and not validator(payload):
            raise ValueError(f"{source} returned an invalid response")

        retrieved_at = datetime.now(timezone.utc).isoformat()
        self._write(
            payload_path,
            metadata_path,
            payload,
            {
                "source": source,
                "endpoint": endpoint,
                "request_hash": key,
                "stored_epoch": now,
                "retrieved_at": retrieved_at,
            },
        )
        self._prune()
        return payload, self._provenance(
            source, endpoint, key, "network", retrieved_at
        )

    @staticmethod
    def _provenance(
        source: str, endpoint: str, key: str, status: str, retrieved_at: object
    ) -> SourceProvenance:
        return SourceProvenance(
            source=source,
            endpoint=endpoint,
            request_hash=key,
            cache_status=status,
            retrieved_at=str(retrieved_at) if retrieved_at else None,
        )

    def _read(
        self,
        payload_path: Path,
        metadata_path: Path,
        validator: Callable[[bytes], bool] | None,
    ) -> tuple[bytes, dict] | None:
        try:
            payload = payload_path.read_bytes()
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not payload or not isinstance(metadata, dict):
                raise ValueError("Incomplete cache entry")
            if validator is not None and not validator(payload):
                raise ValueError("Invalid cached response")
            os.utime(payload_path, None)
            return payload, metadata
        except (OSError, ValueError, json.JSONDecodeError):
            payload_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            return None

    def _write(
        self, payload_path: Path, metadata_path: Path, payload: bytes, metadata: dict
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        for path, data in (
            (payload_path, payload),
            (metadata_path, json.dumps(metadata, sort_keys=True).encode("utf-8")),
        ):
            fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.directory)
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary_path.replace(path)
            finally:
                temporary_path.unlink(missing_ok=True)

    def _prune(self) -> None:
        try:
            payloads = sorted(
                self.directory.glob("*.bin"), key=lambda path: path.stat().st_atime
            )
            total = sum(path.stat().st_size for path in payloads)
            for path in payloads:
                if total <= self.max_bytes:
                    break
                total -= path.stat().st_size
                path.unlink(missing_ok=True)
                path.with_suffix(".json").unlink(missing_ok=True)
        except OSError:
            return

    def clear(self) -> int:
        removed = 0
        if not self.directory.exists():
            return removed
        for pattern in ("*.bin", "*.json"):
            for path in self.directory.glob(pattern):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed


def write_provenance_sidecar(output_path: str | Path, records: list[SourceProvenance]) -> Path:
    destination = Path(output_path).with_suffix(Path(output_path).suffix + ".provenance.json")
    document = {
        "schema": "memorymap-source-provenance-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [record.to_dict() for record in records],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(destination)
    return destination
