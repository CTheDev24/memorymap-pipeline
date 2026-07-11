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
        "motorway_link": 2.0,
        "trunk": 2.0,
        "trunk_link": 1.8,
        "primary": 2.0,
        "primary_link": 1.8,
        "secondary": 1.6,
        "secondary_link": 1.4,
        "tertiary": 1.4,
        "tertiary_link": 1.2,
        "residential": 1.1,
        "living_street": 1.0,
        "unclassified": 1.0,
        "service": 0.8,
    },
    # which highway types to keep by default
    "road_types": ["motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link", "residential", "living_street", "unclassified", "service"],
    # debug plotting for roads
    "roads_debug": False,
    # radius (meters) to query OSM around route center when fetching roads
    "road_query_radius_m": 1000,
    # building footprint generation
    # max real-world extrusion height at print scale (1.25 inches)
    "max_print_height_mm": 31.75,
    # minimum extrusion so 1-storey buildings remain visible
    "min_building_height_mm": 0.4,
    # fallback real-world height when OSM height/levels tags are absent (metres, ~2 storeys)
    "building_default_height_m": 6.0,
    # metres per floor when deriving height from building:levels
    "building_levels_to_m": 3.0,
    # cap on raw OSM height tag to reject corrupt/erroneous values (metres)
    "building_max_real_height_m": 400.0,
    # omit building if this fraction (or more) of its footprint lies outside the margin-inset build area
    "building_clip_threshold": 0.5,
    # retained for backwards compatibility but superseded by proportional height system
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
