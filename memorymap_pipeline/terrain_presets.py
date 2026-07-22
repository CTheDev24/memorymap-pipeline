"""Named, conservative terrain configurations for common map subjects.

Presets are deliberately ordinary configuration dictionaries.  They are applied before
explicit user values so selecting a preset never prevents a CLI, project, or desktop user
from tuning an individual setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TerrainPreset:
    key: str
    label: str
    description: str
    settings: Mapping[str, Any]


TERRAIN_PRESETS: dict[str, TerrainPreset] = {
    "flat-urban": TerrainPreset(
        key="flat-urban",
        label="Flat Urban",
        description="Preserves subtle city and river relief without making roads wavy.",
        settings={
            "terrain_max_relief_mm": 3.0,
            "terrain_min_relief_mm": 2.5,
            "terrain_grid_size": 96,
            "water_recess_mm": 0.4,
            "route_terrain_smoothing_distance_mm": 1.5,
        },
    ),
    "rolling-terrain": TerrainPreset(
        key="rolling-terrain",
        label="Rolling Terrain",
        description="Balances visible hills with supported routes and major roads.",
        settings={
            "terrain_max_relief_mm": 3.0,
            "terrain_min_relief_mm": 1.75,
            "terrain_grid_size": 112,
            "water_recess_mm": 0.4,
            "route_terrain_smoothing_distance_mm": 1.75,
            "road_terrain_max_edge_mm": 3.5,
        },
    ),
    "mountain-coast": TerrainPreset(
        key="mountain-coast",
        label="Mountain / Coast",
        description="Adds coastal and mountain definition while grading printable overlays.",
        settings={
            "terrain_max_relief_mm": 4.0,
            "terrain_min_relief_mm": 2.0,
            "terrain_grid_size": 128,
            "water_recess_mm": 0.4,
            "route_terrain_smoothing_distance_mm": 2.25,
            "road_terrain_max_edge_mm": 3.0,
            "road_terrain_smoothing_distances_mm": {
                "motorway": 8.0,
                "motorway_link": 5.0,
                "trunk": 6.5,
                "trunk_link": 4.5,
            },
        },
    ),
}


def preset_settings(key: str | None) -> dict[str, Any]:
    """Return a mutable copy of a preset, or no settings for legacy/adaptive mode."""
    if key in (None, "", "adaptive"):
        return {}
    try:
        preset = TERRAIN_PRESETS[key]
    except KeyError as exc:
        choices = ", ".join(TERRAIN_PRESETS)
        raise ValueError(f"Unknown terrain preset {key!r}; expected one of: {choices}") from exc
    return {
        name: dict(value) if isinstance(value, Mapping) else value
        for name, value in preset.settings.items()
    }
