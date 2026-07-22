from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .terrain_presets import preset_settings


DEFAULT_CONFIG = {
    "portrait": {"map_width": 190.0, "map_height": 240.0},
    "landscape": {"map_width": 240.0, "map_height": 190.0},
    "route_height": 2.0,
    "route_width": 1.2,
    # Route tops share one elevation across their width and smooth only along travel.
    "route_terrain_smoothing_distance_mm": 1.5,
    "route_mesh_max_edge_mm": 2.4,
    "base_thickness": 1.6,
    # overlap raised features into the base; feature heights remain visible heights
    "feature_embed_depth": 0.2,
    "margin": 5.0,
    # terrain/water scaffold (disabled until selected by a client)
    "terrain_enabled": False,
    "terrain_preset": None,
    "terrain_provider": "usgs-3dep",
    "terrain_grid_size": 96,
    "terrain_request_timeout_seconds": 20.0,
    "terrain_request_attempts": 3,
    "terrain_retry_backoff_seconds": 0.5,
    "terrain_fallback_provider": "aws-terrarium",
    "terrain_fallback_zoom": 12,
    "terrain_fallback_timeout_seconds": 15.0,
    "terrain_fallback_attempts": 2,
    "terrain_flat_fallback": True,
    # Reproducible source cache. An empty path selects the writable user cache.
    "source_cache_dir": "",
    "source_cache_ttl_hours": 168.0,
    "source_cache_max_mb": 512,
    "terrain_max_relief_mm": 3.0,
    "terrain_min_relief_mm": 1.5,
    "water_enabled": False,
    "water_recess_mm": 0.4,
    # 0.2 mm remains exclusively gray above 0.4 mm embedded in white support
    "water_mesh_thickness_mm": 0.6,
    "water_support_overlap_mm": 0.4,
    # minimum white material retained below every water body
    "water_base_skin_mm": 0.4,
    "water_shoreline_tolerance_mm": 0.1,
    # Road generation defaults
    "road_height": 0.8,
    # Low-pass only the top of major roads while their underside remains terrain-supported.
    "road_terrain_smoothing_types": [
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
    ],
    "road_terrain_smoothing_radius_mm": 2.0,
    "road_terrain_min_visible_height_mm": 0.4,
    "road_terrain_max_edge_mm": 4.0,
    "road_terrain_refinement_passes": 5,
    "road_terrain_smoothing_distances_mm": {
        "motorway": 6.0,
        "motorway_link": 4.0,
        "trunk": 5.0,
        "trunk_link": 3.5,
    },
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
        "pedestrian": 1.0,
        "cycleway": 0.7,
        "footway": 0.6,
        "path": 0.5,
        "track": 0.7,
    },
    # which highway types to keep by default
    "road_types": ["motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link", "residential", "living_street", "unclassified", "service", "pedestrian", "cycleway", "footway", "path", "track"],
    # "all" includes pedestrian, cycle, path, and track networks; road_types controls output.
    "road_network_type": "all",
    # debug plotting for roads
    "roads_debug": False,
    # radius (meters) to query OSM around route center when fetching roads
    "road_query_radius_m": 1000,
    # building footprint generation
    # adaptive hard cap for visible building height (user-adjustable up to 1.25 inches)
    "max_print_height_mm": 25.0,
    "building_vertical_exaggeration": 1.0,
    # prevent unsupported min_height volumes in support-free map prints
    "extend_elevated_building_parts_to_ground": True,
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
    config = deepcopy(DEFAULT_CONFIG)
    if config_path is None:
        return config

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    loaded = json.loads(path.read_text(encoding="utf-8"))
    for key, value in preset_settings(loaded.get("terrain_preset")).items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value

    return config
