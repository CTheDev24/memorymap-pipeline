from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import requests
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from memorymap_pipeline.impact_observatory import (
    COLLECTION,
    ImpactObservatoryError,
    ImpactObservatoryProvider,
)
from memorymap_pipeline.landcover import LandCoverClass, LandCoverRequest


def _geotiff(values: np.ndarray, bounds=(0.0, 0.0, 2.0, 2.0)) -> bytes:
    rows, columns = values.shape
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff", height=rows, width=columns, count=1, dtype="uint8",
            crs="EPSG:4326", transform=from_bounds(*bounds, columns, rows), nodata=0,
        ) as dataset:
            dataset.write(values.astype(np.uint8), 1)
        return memory.read()


class _Response:
    def __init__(self, *, payload=None, content=b"", error=None):
        self.payload, self.content, self.error = payload, content, error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload

    def iter_content(self, chunk_size=1024):
        del chunk_size
        yield self.content[:7]
        yield self.content[7:]


class _Http:
    def __init__(self, tif: bytes, *, failure=None):
        self.tif, self.failure = tif, failure
        self.posts, self.gets = [], []

    def post(self, url, *, json, timeout):
        self.posts.append((url, json, timeout))
        if self.failure:
            return _Response(error=self.failure)
        return _Response(payload={"features": [{
            "id": "2023-io-test",
            "assets": {"map": {"href": "https://blob/source.tif"}},
        }]})

    def get(self, url, *, params=None, stream=False, timeout):
        self.gets.append((url, params, stream, timeout))
        if "api/sas" in url:
            return _Response(payload={"href": "https://blob/source.tif?sig=secret"})
        return _Response(content=self.tif)


def _request():
    return LandCoverRequest((0, 0, 20, 20), 2, 2, (0, 0, 2, 2))


def test_provider_searches_signs_normalizes_and_reuses_cache(tmp_path: Path) -> None:
    http = _Http(_geotiff(np.array([[1, 2], [7, 8]], dtype=np.uint8)))
    provider = ImpactObservatoryProvider(tmp_path, http=http)

    first = provider.get_land_cover(_request())
    assert first.classes.tolist() == [
        [LandCoverClass.BUILT, LandCoverClass.BARE],
        [LandCoverClass.WATER, LandCoverClass.VEGETATION],
    ]
    assert first.provenance.provider == "impact-observatory"
    assert first.provenance.edition == "2023"
    assert not first.provenance.cached
    assert http.posts[0][1]["collections"] == [COLLECTION]
    assert "2023-01-01" in http.posts[0][1]["datetime"]
    metadata = json.loads(tmp_path.joinpath("2023-io-test.json").read_text())
    assert "sig=secret" not in json.dumps(metadata)
    assert len(metadata["sha256"]) == 64

    second = provider.get_land_cover(_request())
    assert second.provenance.cached
    assert np.array_equal(second.classes, first.classes)
    assert len(http.posts) == 1
    assert len(http.gets) == 2


def test_provider_ignores_items_from_other_editions(tmp_path: Path) -> None:
    class MixedYears(_Http):
        def post(self, url, *, json, timeout):
            response = super().post(url, json=json, timeout=timeout)
            response.payload["features"].insert(
                0,
                {
                    "id": "10S-2022",
                    "assets": {"map": {"href": "https://blob/2022.tif"}},
                },
            )
            return response

    http = MixedYears(_geotiff(np.array([[1, 2], [7, 8]], dtype=np.uint8)))
    grid = ImpactObservatoryProvider(tmp_path, http=http).get_land_cover(_request())

    assert grid.provenance.details["items"] == "2023-io-test"
    assert not tmp_path.joinpath("10S-2022.tif").exists()

def test_corrupt_cached_asset_is_downloaded_again(tmp_path: Path) -> None:
    http = _Http(_geotiff(np.array([[8]], dtype=np.uint8), (0, 0, 2, 2)))
    provider = ImpactObservatoryProvider(tmp_path, http=http)
    provider.get_land_cover(_request())
    tmp_path.joinpath("2023-io-test.tif").write_bytes(b"corrupt")
    refreshed = provider.get_land_cover(_request())
    assert not refreshed.provenance.cached
    assert len(http.gets) == 4


def test_partial_download_is_removed_on_failure(tmp_path: Path) -> None:
    provider = ImpactObservatoryProvider(tmp_path, http=_Http(b""))
    with pytest.raises(ImpactObservatoryError, match="empty asset"):
        provider.get_land_cover(_request())
    assert not list(tmp_path.glob("*.part"))


def test_stac_failure_is_wrapped(tmp_path: Path) -> None:
    provider = ImpactObservatoryProvider(
        tmp_path, http=_Http(b"", failure=requests.ConnectionError("offline"))
    )
    with pytest.raises(ImpactObservatoryError, match="Unable to search"):
        provider.get_land_cover(_request())


def test_geographic_bounds_are_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="geographic_bounds"):
        ImpactObservatoryProvider(tmp_path, http=_Http(b"")).get_land_cover(
            LandCoverRequest((0, 0, 1, 1), 1, 1)
        )
