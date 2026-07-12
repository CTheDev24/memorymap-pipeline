from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
import requests

from .terrain import ElevationGrid


USGS_3DEP_EXPORT_URL = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/"
    "3DEPElevation/ImageServer/exportImage"
)


class Usgs3depProvider:
    """Fetch bare-earth DEM samples from the official USGS 3DEP ImageServer."""

    name = "usgs-3dep"

    def __init__(self, session: Any | None = None, timeout_seconds: float = 60.0) -> None:
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def fetch(
        self,
        bounds: tuple[float, float, float, float],
        grid_size: tuple[int, int],
        cache_dir: Path,
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
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key = sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()
        cache_path = cache_dir / f"usgs-3dep-{cache_key}.tif"
        if cache_path.is_file():
            payload = cache_path.read_bytes()
        else:
            metadata_response = self.session.get(
                USGS_3DEP_EXPORT_URL,
                params=request,
                timeout=self.timeout_seconds,
            )
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            if "error" in metadata or not metadata.get("href"):
                raise RuntimeError(f"USGS 3DEP export failed: {metadata.get('error', metadata)}")
            image_response = self.session.get(metadata["href"], timeout=self.timeout_seconds)
            image_response.raise_for_status()
            payload = image_response.content
            cache_path.write_bytes(payload)

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
