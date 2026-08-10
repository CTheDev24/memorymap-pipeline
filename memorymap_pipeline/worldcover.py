"""Cached ESA WorldCover 2021 provider.

WorldCover v200 is published as public, unauthenticated 3-by-3 degree GeoTIFF
tiles. This reader uses Pillow's windowed TIFF decoding and nearest-neighbor
sampling, avoiding an additional native raster dependency.
"""

from __future__ import annotations

import math
import os
import threading
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import numpy as np
import requests
from PIL import Image, UnidentifiedImageError

from .landcover import (
    LandCoverClass,
    LandCoverGrid,
    LandCoverProvenance,
    LandCoverRequest,
)

DATASET = "ESA WorldCover"
EDITION = "2021 v200"
TILE_DEGREES = 3
BASE_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"


class WorldCoverError(RuntimeError):
    """Raised when WorldCover data cannot be fetched or decoded."""


class HttpResponse(Protocol):
    status_code: int

    def iter_content(self, chunk_size: int = ...) -> object: ...

    def raise_for_status(self) -> None: ...


class HttpClient(Protocol):
    def get(self, url: str, *, stream: bool, timeout: tuple[float, float]) -> HttpResponse: ...


def worldcover_tile_name(latitude: int, longitude: int) -> str:
    """Return the official v200 filename for a 3-degree southwest tile anchor."""

    lat_prefix = "N" if latitude >= 0 else "S"
    lon_prefix = "E" if longitude >= 0 else "W"
    return (
        f"ESA_WorldCover_10m_2021_v200_"
        f"{lat_prefix}{abs(latitude):02d}{lon_prefix}{abs(longitude):03d}_Map.tif"
    )


def worldcover_tiles(
    bounds_wgs84: tuple[float, float, float, float],
) -> list[tuple[int, int]]:
    """List intersecting tile anchors in deterministic south-to-north order."""

    west, south, east, north = bounds_wgs84
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("WorldCover bounds must be ordered WGS84 longitude/latitude")
    # ``nextafter`` prevents an exact eastern/northern boundary selecting the next tile.
    last_lon = math.floor(math.nextafter(east, west) / TILE_DEGREES) * TILE_DEGREES
    last_lat = math.floor(math.nextafter(north, south) / TILE_DEGREES) * TILE_DEGREES
    first_lon = math.floor(west / TILE_DEGREES) * TILE_DEGREES
    first_lat = math.floor(south / TILE_DEGREES) * TILE_DEGREES
    return [
        (latitude, longitude)
        for latitude in range(first_lat, last_lat + 1, TILE_DEGREES)
        for longitude in range(first_lon, last_lon + 1, TILE_DEGREES)
    ]


_NORMALIZED = np.full(256, LandCoverClass.UNKNOWN.value, dtype=np.uint8)
for _code in (10, 20, 30, 40, 90, 95, 100):
    _NORMALIZED[_code] = LandCoverClass.VEGETATION.value
_NORMALIZED[50] = LandCoverClass.BUILT.value
for _code in (60, 70):
    _NORMALIZED[_code] = LandCoverClass.BARE.value
_NORMALIZED[80] = LandCoverClass.WATER.value

_PILLOW_LIMIT_LOCK = threading.Lock()


@dataclass
class WorldCoverProvider:
    """Fetch, cache, and sample public ESA WorldCover v200 tiles."""

    cache_dir: Path
    http: HttpClient = field(default_factory=requests.Session)
    base_url: str = BASE_URL
    timeout: tuple[float, float] = (10.0, 120.0)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)

    def _cached_tile(self, latitude: int, longitude: int) -> tuple[Path, bool]:
        filename = worldcover_tile_name(latitude, longitude)
        destination = self.cache_dir / filename
        if destination.is_file() and destination.stat().st_size:
            return destination, True
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            response = self.http.get(
                f"{self.base_url.rstrip('/')}/{filename}",
                stream=True,
                timeout=self.timeout,
            )
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
            if not temporary.stat().st_size:
                raise WorldCoverError(f"WorldCover returned an empty tile: {filename}")
            os.replace(temporary, destination)
        except (OSError, requests.RequestException, WorldCoverError) as exc:
            temporary.unlink(missing_ok=True)
            if isinstance(exc, WorldCoverError):
                raise
            raise WorldCoverError(f"Unable to cache WorldCover tile {filename}: {exc}") from exc
        return destination, False

    def get_land_cover(self, request: LandCoverRequest) -> LandCoverGrid:
        bounds = request.geographic_bounds_wgs84
        if bounds is None:
            raise ValueError("WorldCover requires geographic_bounds_wgs84 on the request")
        west, south, east, north = bounds
        rows, columns = request.rows, request.columns
        longitudes = west + (np.arange(columns) + 0.5) * ((east - west) / columns)
        latitudes = south + (np.arange(rows) + 0.5) * ((north - south) / rows)
        result = np.full((rows, columns), LandCoverClass.UNKNOWN.value, dtype=np.uint8)
        cached_states: list[bool] = []
        names: list[str] = []
        checksums: list[str] = []
        cache_timestamps: list[str] = []

        for tile_lat, tile_lon in worldcover_tiles(bounds):
            path, was_cached = self._cached_tile(tile_lat, tile_lon)
            cached_states.append(was_cached)
            names.append(path.name)
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            checksums.append(digest.hexdigest())
            cache_timestamps.append(
                datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            )
            selected_columns = np.flatnonzero(
                (longitudes >= tile_lon) & (longitudes < tile_lon + TILE_DEGREES)
            )
            selected_rows = np.flatnonzero(
                (latitudes >= tile_lat) & (latitudes < tile_lat + TILE_DEGREES)
            )
            if not selected_columns.size or not selected_rows.size:
                continue
            try:
                # Official 10 m tiles legitimately exceed Pillow's generic image-size
                # threshold. Limit relaxation is serialized and restricted to cached
                # files from this provider; the previous process setting is restored.
                with _PILLOW_LIMIT_LOCK:
                    previous_limit = Image.MAX_IMAGE_PIXELS
                    Image.MAX_IMAGE_PIXELS = None
                    try:
                        with Image.open(path) as image:
                            width, height = image.size
                            pixel_columns = np.floor(
                                (longitudes[selected_columns] - tile_lon)
                                / TILE_DEGREES
                                * width
                            ).astype(int)
                            pixel_rows = np.floor(
                                (tile_lat + TILE_DEGREES - latitudes[selected_rows])
                                / TILE_DEGREES
                                * height
                            ).astype(int)
                            pixel_columns = np.clip(pixel_columns, 0, width - 1)
                            pixel_rows = np.clip(pixel_rows, 0, height - 1)
                            left, right = int(pixel_columns.min()), int(pixel_columns.max()) + 1
                            top, bottom = int(pixel_rows.min()), int(pixel_rows.max()) + 1
                            window = np.asarray(
                                image.crop((left, top, right, bottom)), dtype=np.uint8
                            )
                            if window.ndim != 2:
                                raise WorldCoverError(
                                    f"WorldCover tile is not categorical: {path.name}"
                                )
                            sampled = window[
                                np.ix_(pixel_rows - top, pixel_columns - left)
                            ]
                            result[np.ix_(selected_rows, selected_columns)] = _NORMALIZED[
                                sampled
                            ]
                    finally:
                        Image.MAX_IMAGE_PIXELS = previous_limit
            except (OSError, UnidentifiedImageError, WorldCoverError) as exc:
                if isinstance(exc, WorldCoverError):
                    raise
                raise WorldCoverError(f"Unable to decode WorldCover tile {path.name}: {exc}") from exc

        return LandCoverGrid(
            result,
            request.bounds_mm,
            LandCoverProvenance(
                provider="esa-worldcover",
                dataset=DATASET,
                edition=EDITION,
                cached=bool(cached_states) and all(cached_states),
                details={
                    "tiles": ",".join(names),
                    "source": self.base_url,
                    "sha256": ",".join(checksums),
                    "cached_at_utc": ",".join(cache_timestamps),
                },
            ),
        )
