"""Operator-facing finish, accent and road-selection settings."""

from ..palettes import LAYER_KEYS, normalize_color

ROAD_CATEGORIES = {
    "Motorways / trunks": ("motorway", "motorway_link", "trunk", "trunk_link"),
    "Main roads": (
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
    ),
    "Residential / local streets": ("residential", "living_street", "unclassified"),
    "Service roads": ("service",),
    "Cycleways": ("cycleway",),
    "Footpaths / tracks": ("footway", "path", "pedestrian", "steps", "track"),
}
ACCENTS = {"Signal Orange": "#F7591F", "Volt Lime": "#C7FF00", "Pulse Pink": "#FF3B8D"}


def finish_colors(finish, accent, custom="#F7591F"):
    if finish not in {"Gallery", "Nocturne"}:
        raise ValueError("Unknown finish")
    stone, charcoal = "#CAC6BC", "#292A2B"
    colors = dict.fromkeys(LAYER_KEYS, stone if finish == "Gallery" else charcoal)
    if finish == "Nocturne":
        colors["roads"] = "#505254"
    if accent == "Neutral":
        route = charcoal if finish == "Gallery" else stone
    elif accent == "Color Wheel":
        route = normalize_color(custom)
    else:
        route = ACCENTS[accent]
    colors["route"] = route
    return colors
