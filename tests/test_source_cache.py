from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from memorymap_pipeline.source_cache import (
    SourceCache,
    default_source_cache_dir,
    stable_request_hash,
    write_provenance_sidecar,
)


def test_stable_request_hash_ignores_mapping_order() -> None:
    first = stable_request_hash("osm", {"bounds": [1, 2, 3, 4], "tags": {"b": 2, "a": 1}})
    second = stable_request_hash("osm", {"tags": {"a": 1, "b": 2}, "bounds": [1, 2, 3, 4]})
    assert first == second


def test_cache_hit_and_stale_fallback_are_reported(tmp_path: Path) -> None:
    cache = SourceCache(tmp_path, ttl_seconds=60)
    calls = 0

    def network() -> bytes:
        nonlocal calls
        calls += 1
        return b"valid"

    first, first_source = cache.fetch(
        source="test", endpoint="https://example.test", request={"x": 1},
        fetcher=network, validator=lambda payload: payload == b"valid",
    )
    second, second_source = cache.fetch(
        source="test", endpoint="https://example.test", request={"x": 1},
        fetcher=network, validator=lambda payload: payload == b"valid",
    )
    assert first == second == b"valid"
    assert first_source.cache_status == "network"
    assert second_source.cache_status == "cache_hit"
    assert calls == 1

    stale_cache = SourceCache(tmp_path, ttl_seconds=0)
    stale, stale_source = stale_cache.fetch(
        source="test", endpoint="https://example.test", request={"x": 1},
        fetcher=lambda: (_ for _ in ()).throw(TimeoutError("offline")),
        validator=lambda payload: payload == b"valid",
    )
    assert stale == b"valid"
    assert stale_source.cache_status == "stale_cache"
    assert stale_source.retrieved_at == first_source.retrieved_at


def test_invalid_network_response_is_never_cached(tmp_path: Path) -> None:
    cache = SourceCache(tmp_path)
    with pytest.raises(ValueError, match="invalid response"):
        cache.fetch(
            source="test", endpoint="https://example.test", request={"x": 1},
            fetcher=lambda: b"bad", validator=lambda payload: payload == b"good",
        )
    assert not list(tmp_path.glob("*.bin"))


def test_cache_prunes_oldest_payloads_to_size_limit(tmp_path: Path) -> None:
    cache = SourceCache(tmp_path, max_bytes=5)
    cache.fetch(source="a", endpoint="a", request=1, fetcher=lambda: b"1111")
    cache.fetch(source="b", endpoint="b", request=2, fetcher=lambda: b"2222")
    assert sum(path.stat().st_size for path in tmp_path.glob("*.bin")) <= 5


def test_provenance_sidecar_is_machine_readable(tmp_path: Path) -> None:
    cache = SourceCache(tmp_path / "cache")
    _, record = cache.fetch(
        source="usgs", endpoint="https://example.test", request={"bounds": [1, 2, 3, 4]},
        fetcher=lambda: b"dem",
    )
    destination = write_provenance_sidecar(tmp_path / "map.3mf", [record])
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["schema"] == "memorymap-source-provenance-v1"
    assert document["sources"][0]["request_hash"] == record.request_hash
    assert document["sources"][0]["cache_status"] == "network"


def test_windows_default_uses_local_app_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
    if os.name == "nt":
        assert default_source_cache_dir() == Path(os.environ["LOCALAPPDATA"]) / "MemoryMap" / "source-cache"
