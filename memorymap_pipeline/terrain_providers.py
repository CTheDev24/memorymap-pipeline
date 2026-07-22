from __future__ import annotations

from io import BytesIO
from pathlib import Path
import time
from typing import Any

import numpy as np
from PIL import Image
import requests

from .terrain import ElevationGrid
from .source_cache import SourceCache, SourceProvenance


USGS_3DEP_EXPORT_URL = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/"
    "3DEPElevation/ImageServer/exportImage"
)


class Usgs3depProvider:
    """Fetch bare-earth DEM samples from the official USGS 3DEP ImageServer."""

    name = "usgs-3dep"

    def __init__(
        self,
        session: Any | None = None,
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
    ) -> None:
        if timeout_seconds <= 0 or max_attempts < 1 or backoff_seconds < 0:
            raise ValueError("Terrain request retry settings are invalid")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.last_provenance: list[SourceProvenance] = []

    def _get(self, url: str, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout_seconds,
                    **kwargs,
                )
                response.raise_for_status()
                return response
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts and self.backoff_seconds:
                    time.sleep(self.backoff_seconds * (2 ** attempt))
        raise RuntimeError(
            f"USGS 3DEP request failed after {self.max_attempts} attempts: {last_error}"
        ) from last_error

    def fetch(
        self,
        bounds: tuple[float, float, float, float],
        grid_size: tuple[int, int],
        cache_dir: Path | SourceCache,
    ) -> ElevationGrid:
        south, north, west, east = bounds
        rows, columns = grid_size
        if south >= north or west >= east or min(rows, columns) < 2:
            raise ValueError("USGS 3DEP request bounds and grid dimensions are invalid")
        if max(rows, columns) > 2048:
            raise ValueError("USGS 3DEP scaffold limits elevation grids to 2048 samples per side")

        request = {
            "bbox": f"{west},{south},{east},{north}",
            "bboxSR": "4326",
            "imageSR": "4326",
            "size": f"{columns},{rows}",
            "format": "tiff",
            "pixelType": "F32",
            "interpolation": "RSP_BilinearInterpolation",
            "f": "json",
        }
        cache = cache_dir if isinstance(cache_dir, SourceCache) else SourceCache(cache_dir)

        def fetch_payload() -> bytes:
            metadata_response = self._get(
                USGS_3DEP_EXPORT_URL,
                params=request,
            )
            metadata = metadata_response.json()
            if "error" in metadata or not metadata.get("href"):
                raise RuntimeError(f"USGS 3DEP export failed: {metadata.get('error', metadata)}")
            image_response = self._get(metadata["href"])
            return image_response.content

        def valid_payload(payload: bytes) -> bool:
            try:
                with Image.open(BytesIO(payload)) as image:
                    return image.size == (columns, rows)
            except (OSError, ValueError):
                return False

        payload, provenance = cache.fetch(
            source=self.name,
            endpoint=USGS_3DEP_EXPORT_URL,
            request=request,
            fetcher=fetch_payload,
            validator=valid_payload,
        )
        self.last_provenance = [provenance]

        with Image.open(BytesIO(payload)) as image:
            elevations = np.asarray(image, dtype=float)
        if elevations.ndim == 3:
            elevations = elevations[..., 0]
        if elevations.shape != (rows, columns):
            raise RuntimeError(
                f"USGS 3DEP returned grid {elevations.shape}; expected {(rows, columns)}"
            )
        elevations[~np.isfinite(elevations) | (elevations < -1e20)] = np.nan
        return ElevationGrid(elevations, south, north, west, east, self.name)

TERRARIUM_TILE_URL = (
    "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
)


def _web_mercator_pixels(
    latitude: np.ndarray, longitude: np.ndarray, zoom: int
) -> tuple[np.ndarray, np.ndarray]:
    scale = float((2 ** zoom) * 256)
    clipped_latitude = np.clip(latitude, -85.05112878, 85.05112878)
    x = (longitude + 180.0) / 360.0 * scale
    radians = np.radians(clipped_latitude)
    y = (
        1.0
        - np.arcsinh(np.tan(radians)) / np.pi
    ) / 2.0 * scale
    return x, y


def _decode_terrarium(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"), dtype=float)
    return rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0


class TerrariumProvider(Usgs3depProvider):
    """Fetch globally available elevation samples from public AWS terrain tiles."""

    name = "aws-terrarium"

    def __init__(self, *args: Any, zoom: int = 12, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not 0 <= zoom <= 15:
            raise ValueError("Terrarium zoom must be between 0 and 15")
        self.zoom = zoom

    def fetch(
        self,
        bounds: tuple[float, float, float, float],
        grid_size: tuple[int, int],
        cache_dir: Path | SourceCache,
    ) -> ElevationGrid:
        south, north, west, east = bounds
        rows, columns = grid_size
        if south >= north or west >= east or min(rows, columns) < 2:
            raise ValueError("Terrarium request bounds and grid dimensions are invalid")

        latitudes = np.linspace(north, south, rows)
        longitudes = np.linspace(west, east, columns)
        longitude_grid, latitude_grid = np.meshgrid(longitudes, latitudes)
        pixel_x, pixel_y = _web_mercator_pixels(
            latitude_grid, longitude_grid, self.zoom
        )
        min_tile_x = int(np.floor(pixel_x.min() / 256.0))
        max_tile_x = int(np.floor((pixel_x.max() + 1.0) / 256.0))
        min_tile_y = int(np.floor(pixel_y.min() / 256.0))
        max_tile_y = int(np.floor((pixel_y.max() + 1.0) / 256.0))

        cache = cache_dir if isinstance(cache_dir, SourceCache) else SourceCache(cache_dir)
        self.last_provenance = []
        tiles: dict[tuple[int, int], np.ndarray] = {}
        for tile_y in range(min_tile_y, max_tile_y + 1):
            for tile_x in range(min_tile_x, max_tile_x + 1):
                url = TERRARIUM_TILE_URL.format(z=self.zoom, x=tile_x, y=tile_y)

                def fetch_tile(url: str = url) -> bytes:
                    response = self._get(
                        url
                    )
                    return response.content

                def valid_tile(payload: bytes) -> bool:
                    try:
                        with Image.open(BytesIO(payload)) as image:
                            return image.size == (256, 256)
                    except (OSError, ValueError):
                        return False

                payload, provenance = cache.fetch(
                    source=self.name,
                    endpoint=url,
                    request={"zoom": self.zoom, "x": tile_x, "y": tile_y},
                    fetcher=fetch_tile,
                    validator=valid_tile,
                )
                self.last_provenance.append(provenance)
                with Image.open(BytesIO(payload)) as image:
                    tiles[(tile_x, tile_y)] = _decode_terrarium(image)

        def pixels(x_index: np.ndarray, y_index: np.ndarray) -> np.ndarray:
            values = np.empty(x_index.shape, dtype=float)
            tile_x = np.floor_divide(x_index, 256)
            tile_y = np.floor_divide(y_index, 256)
            local_x = np.mod(x_index, 256)
            local_y = np.mod(y_index, 256)
            for key, tile in tiles.items():
                mask = (tile_x == key[0]) & (tile_y == key[1])
                values[mask] = tile[local_y[mask], local_x[mask]]
            return values

        x0 = np.floor(pixel_x).astype(int)
        y0 = np.floor(pixel_y).astype(int)
        x_fraction = pixel_x - x0
        y_fraction = pixel_y - y0
        top = pixels(x0, y0) * (1.0 - x_fraction) + pixels(
            x0 + 1, y0
        ) * x_fraction
        bottom = pixels(x0, y0 + 1) * (1.0 - x_fraction) + pixels(
            x0 + 1, y0 + 1
        ) * x_fraction
        elevations = top * (1.0 - y_fraction) + bottom * y_fraction
        return ElevationGrid(elevations, south, north, west, east, self.name)
