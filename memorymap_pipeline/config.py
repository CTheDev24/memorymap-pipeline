from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "portrait": {"map_width": 190.0, "map_height": 240.0},
    "landscape": {"map_width": 240.0, "map_height": 190.0},
    "route_height": 2.0,
    "route_width": 1.2,
    "base_thickness": 1.0,
    "margin": 8.0,
    # Road generation defaults
    "road_height": 0.8,
    # widths in mm by highway type
    "road_widths": {
        "motorway": 2.4,
        "trunk": 2.0,
        "primary": 2.0,
        "secondary": 1.6,
        "tertiary": 1.4,
        "residential": 1.1,
    },
    # which highway types to keep by default
    "road_types": ["motorway", "trunk", "primary", "secondary", "tertiary", "residential"],
    # debug plotting for roads
    "roads_debug": False,
    # radius (meters) to query OSM around route center when fetching roads
    "road_query_radius_m": 1000,
    # building footprint verification
    "building_thickness": 0.3,
    "buildings_debug": False,
}


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    if config_path is None:
        return config

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    loaded = json.loads(path.read_text(encoding="utf-8"))
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value

    return config
