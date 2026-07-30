from __future__ import annotations

import re
from collections.abc import Mapping


LAYER_KEYS = ("base", "route", "roads", "buildings", "water", "landscape")

PALETTE_PRESETS: dict[str, dict[str, str]] = {
    "urban-classic": {
        "base": "#FFFFFF",
        "route": "#FF6633",
        "roads": "#000000",
        "buildings": "#808080",
        "water": "#808080",
        "landscape": "#4F772D",
    },
    "landscape-classic": {
        "base": "#D6CBAB",
        "route": "#FF6633",
        "roads": "#000000",
        "buildings": "#808080",
        "water": "#3399FF",
        "landscape": "#4F772D",
    },
}

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")


def default_preset(style_profile: str) -> str:
    if style_profile not in {"urban", "landscape"}:
        raise ValueError(f"Unsupported style profile: {style_profile}")
    return f"{style_profile}-classic"


def normalize_color(value: str) -> str:
    if not isinstance(value, str) or _HEX_COLOR.fullmatch(value) is None:
        raise ValueError(f"Invalid layer color: {value!r}")
    return value[:7].upper()


def resolve_palette(
    style_profile: str = "urban",
    preset: str | None = None,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    preset_name = preset or default_preset(style_profile)
    if preset_name not in PALETTE_PRESETS:
        raise ValueError(f"Unsupported color preset: {preset_name}")
    colors = dict(PALETTE_PRESETS[preset_name])
    for key, value in (overrides or {}).items():
        if key not in LAYER_KEYS:
            raise ValueError(f"Unsupported color layer: {key}")
        colors[key] = normalize_color(value)
    return colors


def rgba(color: str) -> tuple[int, int, int, int]:
    value = normalize_color(color)
    return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5)) + (255,)
