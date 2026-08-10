from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
import requests
from PIL import Image

from memorymap_pipeline.landcover import LandCoverClass, LandCoverRequest
from memorymap_pipeline.worldcover import (
    WorldCoverError,
    WorldCoverProvider,
    worldcover_tile_name,
    worldcover_tiles,
)


def _tiff_bytes(values: np.ndarray) -> bytes:
    output = BytesIO()
    Image.fromarray(np.asarray(values, dtype=np.uint8)).save(output, format="TIFF")
    return output.getvalue()


class _Response:
    def __init__(self, payload: bytes, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.status_code = 200

    def raise_for_status(self) -> None:
        if self.error:
            raise self.error

    def iter_content(self, chunk_size: int = 1024) -> object:
        del chunk_size
        yield self.payload[:5]
        yield self.payload[5:]


class _Http:
    def __init__(self, payloads: dict[str, bytes], error: Exception | None = None) -> None:
        self.payloads = payloads
        self.error = error
        self.calls: list[str] = []

    def get(self, url: str, *, stream: bool, timeout: tuple[float, float]) -> _Response:
        assert stream
        assert timeout == (10.0, 120.0)
        self.calls.append(url)
        return _Response(self.payloads.get(Path(url).name, b""), self.error)


def _request(geo: tuple[float, float, float, float], rows: int, columns: int):
    return LandCoverRequest((0, 0, 40, 30), rows, columns, geo)


def test_tile_names_and_selection_handle_hemispheres_and_exact_edges() -> None:
    assert worldcover_tile_name(-3, -123).endswith("S03W123_Map.tif")
    assert worldcover_tile_name(0, 3).endswith("N00E003_Map.tif")
    assert worldcover_tiles((-123.0, 36.0, -120.0, 39.0)) == [(36, -123)]
    assert worldcover_tiles((-123.1, -0.1, -119.9, 3.1)) == [
        (-3, -126),
        (-3, -123),
        (-3, -120),
        (0, -126),
        (0, -123),
        (0, -120),
        (3, -126),
        (3, -123),
        (3, -120),
    ]


def test_provider_downloads_crops_normalizes_and_then_uses_cache(tmp_path: Path) -> None:
    values = np.array([[10, 60, 80], [50, 30, 70], [80, 10, 60]], dtype=np.uint8)
    name = worldcover_tile_name(0, 0)
    http = _Http({name: _tiff_bytes(values)})
    provider = WorldCoverProvider(tmp_path, http=http)

    first = provider.get_land_cover(_request((0, 0, 3, 3), 3, 3))
    assert np.array_equal(
        first.classes,
        np.array(
            [
                [LandCoverClass.WATER, LandCoverClass.VEGETATION, LandCoverClass.BARE],
                [LandCoverClass.BUILT, LandCoverClass.VEGETATION, LandCoverClass.BARE],
                [LandCoverClass.VEGETATION, LandCoverClass.BARE, LandCoverClass.WATER],
            ],
            dtype=np.uint8,
        ),
    )
    assert not first.provenance.cached
    assert first.provenance.edition == "2021 v200"
    assert len(first.provenance.details["sha256"]) == 64
    assert first.provenance.details["cached_at_utc"].endswith("+00:00")
    assert len(http.calls) == 1

    second = provider.get_land_cover(_request((0, 0, 3, 3), 3, 3))
    assert second.provenance.cached
    assert np.array_equal(second.classes, first.classes)
    assert len(http.calls) == 1


def test_provider_selects_and_merges_multiple_tiles(tmp_path: Path) -> None:
    west_name = worldcover_tile_name(0, 0)
    east_name = worldcover_tile_name(0, 3)
    http = _Http(
        {
            west_name: _tiff_bytes(np.full((3, 3), 60, dtype=np.uint8)),
            east_name: _tiff_bytes(np.full((3, 3), 80, dtype=np.uint8)),
        }
    )
    grid = WorldCoverProvider(tmp_path, http=http).get_land_cover(
        _request((2.5, 1.0, 3.5, 2.0), 1, 4)
    )
    assert grid.classes.tolist() == [[2, 2, 8, 8]]
    assert len(http.calls) == 2
    assert west_name in grid.provenance.details["tiles"]
    assert east_name in grid.provenance.details["tiles"]


def test_provider_requires_geographic_bounds(tmp_path: Path) -> None:
    provider = WorldCoverProvider(tmp_path, http=_Http({}))
    with pytest.raises(ValueError, match="geographic_bounds"):
        provider.get_land_cover(LandCoverRequest((0, 0, 1, 1), 1, 1))


def test_network_failure_is_wrapped_and_partial_file_removed(tmp_path: Path) -> None:
    http = _Http({}, requests.ConnectionError("offline"))
    provider = WorldCoverProvider(tmp_path, http=http)
    with pytest.raises(WorldCoverError, match="Unable to cache"):
        provider.get_land_cover(_request((0, 0, 1, 1), 1, 1))
    assert not list(tmp_path.glob("*.part"))


def test_corrupt_cached_tile_has_clear_decode_error(tmp_path: Path) -> None:
    name = worldcover_tile_name(0, 0)
    tmp_path.joinpath(name).write_bytes(b"not a tiff")
    provider = WorldCoverProvider(tmp_path, http=_Http({}))
    with pytest.raises(WorldCoverError, match="Unable to decode"):
        provider.get_land_cover(_request((0, 0, 1, 1), 1, 1))
