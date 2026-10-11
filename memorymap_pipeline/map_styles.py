"""Presentation styles resolved onto the established geometry engines.

None is the compatibility path: old callers and projects keep their exact layer
and profile settings until an operator explicitly selects a new style.
"""
from __future__ import annotations

from dataclasses import replace
from math import isfinite

MAP_STYLES = {"cityscape": "Cityscape", "terrain": "Terrain", "street_grid": "Street Grid"}
ROAD_CLASSES = {
    "motorways": ("motorway", "motorway_link", "trunk", "trunk_link"),
    "main": ("primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link"),
    "local": ("residential", "living_street", "unclassified", "pedestrian", "cycleway", "footway", "path", "track"),
    "service": ("service",),
}
ROAD_CLASS_NAMES = {
    "motorways": "Motorways / Trunks", "main": "Main Roads",
    "local": "Residential / Local Roads", "service": "Service Roads",
}
EXPERIMENTAL_WIDTHS = {"motorways": 1.2, "main": 0.8, "local": 0.4, "service": 0.4}


def road_class(highway: str) -> str:
    for name, types in ROAD_CLASSES.items():
        if highway in types:
            return name
    raise ValueError(f"Unsupported road classification: {highway}")


def class_widths(values: dict | None = None) -> dict[str, float]:
    widths = dict(EXPERIMENTAL_WIDTHS)
    if values:
        if set(values) - set(widths):
            raise ValueError("Unknown road width class")
        widths.update(values)
    widths = {key: float(value) for key, value in widths.items()}
    if any(not isfinite(value) or value <= 0 for value in widths.values()):
        raise ValueError("Road class widths must be positive and finite")
    return widths


def resolve_request(request):
    """Apply layer identity only for explicitly selected styles."""
    if request.map_style is None:
        return request
    if request.map_style not in MAP_STYLES:
        raise ValueError(f"Unsupported Map Style: {request.map_style}")
    terrain = request.map_style == "terrain"
    config = dict(request.config)
    config.update(map_style=request.map_style, style_profile="landscape" if terrain else "urban",
                  terrain_enabled=terrain)
    if terrain:
        # A selected terrain style must not silently succeed with a flat DEM.
        config["terrain_flat_fallback"] = False
    else:
        config["road_class_widths_mm"] = class_widths(config.get("road_class_widths_mm"))
        config["road_class_meshes"] = True
        config.setdefault("adaptive_streets", request.map_style == "street_grid")
    return replace(request, include_buildings=request.map_style == "cityscape", config=config)
