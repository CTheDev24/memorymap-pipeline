"""Cached Impact Observatory annual 10 m land-cover provider.

The public Planetary Computer STAC and SAS APIs require no user credentials.
Signed asset URLs are deliberately short-lived and are never written to cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import requests

from .landcover import LandCoverClass, LandCoverGrid, LandCoverProvenance, LandCoverRequest

COLLECTION = "io-lulc-annual-v02"
DATASET = "Impact Observatory Annual Land Use/Land Cover"
EDITION = "2023"
STAC_SEARCH_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS_SIGN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"


class ImpactObservatoryError(RuntimeError):
    """Raised when Impact Observatory data cannot be discovered or decoded."""


class HttpResponse(Protocol):
    def raise_for_status(self) -> None: ...
    def json(self) -> Any: ...
    def iter_content(self, chunk_size: int = ...) -> object: ...


class HttpClient(Protocol):
    def post(self, url: str, *, json: dict[str, Any], timeout: tuple[float, float]) -> HttpResponse: ...
    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = ...,
        stream: bool = ...,
        timeout: tuple[float, float],
    ) -> HttpResponse: ...


_NORMALIZED = np.full(256, LandCoverClass.UNKNOWN.value, dtype=np.uint8)
_NORMALIZED[1] = LandCoverClass.WATER.value
for _code in (2, 4, 5, 11):
    _NORMALIZED[_code] = LandCoverClass.VEGETATION.value
_NORMALIZED[7] = LandCoverClass.BUILT.value
_NORMALIZED[8] = LandCoverClass.BARE.value
_NORMALIZED[9] = LandCoverClass.BARE.value
# Code 10 is clouds; retaining UNKNOWN is safer than inventing ground cover.


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "item"


def _query_key(bounds: tuple[float, float, float, float]) -> str:
    canonical = json.dumps({"bounds": list(bounds), "edition": EDITION}, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _edition_item(item: dict[str, str]) -> bool:
    return EDITION in str(item.get("id", ""))


@dataclass
class ImpactObservatoryProvider:
    """Discover, cache, and categorically sample Impact Observatory COGs."""

    cache_dir: Path
    http: HttpClient = field(default_factory=requests.Session)
    search_url: str = STAC_SEARCH_URL
    sign_url: str = SAS_SIGN_URL
    timeout: tuple[float, float] = (10.0, 120.0)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)

    def _manifest_path(self, bounds: tuple[float, float, float, float]) -> Path:
        return self.cache_dir / f"query-{_query_key(bounds)}.json"

    def _search(self, bounds: tuple[float, float, float, float]) -> tuple[list[dict[str, str]], bool]:
        manifest_path = self._manifest_path(bounds)
        if manifest_path.is_file():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                if payload.get("collection") == COLLECTION and payload.get("edition") == EDITION:
                    items = payload.get("items")
                    if isinstance(items, list):
                        items = [item for item in items if _edition_item(item)]
                        if items:
                            return items, True
            except (OSError, json.JSONDecodeError):
                pass

        try:
            response = self.http.post(
                self.search_url,
                json={
                    "collections": [COLLECTION],
                    "bbox": list(bounds),
                    "datetime": "2023-01-01T00:00:00Z/2023-12-31T23:59:59Z",
                    "limit": 100,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            features = response.json().get("features", [])
            items: list[dict[str, str]] = []
            for feature in features:
                assets = feature.get("assets", {})
                asset = assets.get("map") or assets.get("data")
                href = asset.get("href") if isinstance(asset, dict) else None
                item = {"id": str(feature.get("id", "")), "href": str(href or "")}
                if item["href"] and _edition_item(item):
                    items.append(item)
            if not items:
                raise ImpactObservatoryError("STAC search returned no 2023 map assets")
            items.sort(key=lambda item: item["id"])
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = manifest_path.with_suffix(".json.part")
            temporary.write_text(
                json.dumps(
                    {
                        "collection": COLLECTION,
                        "edition": EDITION,
                        "bounds_wgs84": list(bounds),
                        "queried_at_utc": datetime.now(timezone.utc).isoformat(),
                        "items": items,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            os.replace(temporary, manifest_path)
            return items, False
        except (OSError, requests.RequestException, ValueError, ImpactObservatoryError) as exc:
            if isinstance(exc, ImpactObservatoryError):
                raise
            raise ImpactObservatoryError(f"Unable to search Impact Observatory STAC: {exc}") from exc

    def _cached_asset(self, item: dict[str, str]) -> tuple[Path, bool, dict[str, str]]:
        stem = _safe_name(item["id"])
        destination = self.cache_dir / f"{stem}.tif"
        metadata_path = self.cache_dir / f"{stem}.json"
        if destination.is_file() and metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata.get("sha256") == _sha256(destination) and destination.stat().st_size:
                    return destination, True, metadata
            except (OSError, json.JSONDecodeError):
                pass
            destination.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tif.part")
        try:
            signed = self.http.get(
                self.sign_url,
                params={"href": item["href"]},
                stream=False,
                timeout=self.timeout,
            )
            signed.raise_for_status()
            signed_href = signed.json().get("href")
            if not signed_href:
                raise ImpactObservatoryError(f"SAS response omitted href for {item['id']}")
            response = self.http.get(
                str(signed_href), params=None, stream=True, timeout=self.timeout
            )
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
            if not temporary.is_file() or not temporary.stat().st_size:
                raise ImpactObservatoryError(f"Impact Observatory returned an empty asset: {item['id']}")
            os.replace(temporary, destination)
            metadata = {
                "item_id": item["id"],
                "edition": EDITION,
                "source_href": item["href"],
                "sha256": _sha256(destination),
                "cached_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
            return destination, False, metadata
        except (OSError, requests.RequestException, ValueError, ImpactObservatoryError) as exc:
            temporary.unlink(missing_ok=True)
            if isinstance(exc, ImpactObservatoryError):
                raise
            raise ImpactObservatoryError(f"Unable to cache Impact Observatory item {item['id']}: {exc}") from exc

    def get_land_cover(self, request: LandCoverRequest) -> LandCoverGrid:
        bounds = request.geographic_bounds_wgs84
        if bounds is None:
            raise ValueError("Impact Observatory requires geographic_bounds_wgs84")
        items, query_cached = self._search(bounds)
        result = np.full((request.rows, request.columns), LandCoverClass.UNKNOWN.value, dtype=np.uint8)
        asset_cached: list[bool] = []
        metadata_rows: list[dict[str, str]] = []

        try:
            import rasterio
            from rasterio.crs import CRS
            from rasterio.enums import Resampling
            from rasterio.transform import from_bounds
            from rasterio.warp import reproject
        except ImportError as exc:  # pragma: no cover - installation error
            raise ImpactObservatoryError("rasterio is required for Impact Observatory data") from exc

        north_up = np.zeros_like(result)
        west, south, east, north = bounds
        transform = from_bounds(west, south, east, north, request.columns, request.rows)
        for item in items:
            path, was_cached, metadata = self._cached_asset(item)
            asset_cached.append(was_cached)
            metadata_rows.append(metadata)
            try:
                with rasterio.open(path) as source:
                    sampled = np.zeros_like(result)
                    reproject(
                        source=rasterio.band(source, 1),
                        destination=sampled,
                        src_transform=source.transform,
                        src_crs=source.crs,
                        dst_transform=transform,
                        dst_crs=CRS.from_epsg(4326),
                        src_nodata=source.nodata,
                        dst_nodata=0,
                        resampling=Resampling.nearest,
                    )
                    selected = sampled != 0
                    north_up[selected] = sampled[selected]
            except (OSError, rasterio.errors.RasterioError) as exc:
                raise ImpactObservatoryError(f"Unable to decode Impact Observatory item {item['id']}: {exc}") from exc

        # Raster grids are north-up; MemoryMap row zero is adjacent to min_y.
        result[:, :] = _NORMALIZED[np.flipud(north_up)]
        return LandCoverGrid(
            result,
            request.bounds_mm,
            LandCoverProvenance(
                provider="impact-observatory",
                dataset=DATASET,
                edition=EDITION,
                cached=query_cached and bool(asset_cached) and all(asset_cached),
                details={
                    "collection": COLLECTION,
                    "items": ",".join(row["item_id"] for row in metadata_rows),
                    "sha256": ",".join(row["sha256"] for row in metadata_rows),
                    "cached_at_utc": ",".join(row["cached_at_utc"] for row in metadata_rows),
                    "resampling": "nearest",
                },
            ),
        )
