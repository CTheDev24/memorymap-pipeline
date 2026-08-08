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
    "heritage": {
        "base": "#F4EFEB",
        "route": "#C06443",
        "roads": "#E4D0B0",
        "buildings": "#BBADA4",
        "water": "#BBADA4",
        "landscape": "#F4EFEB",
    },
    "gallery-concrete": {
        "base": "#F4EFEB",
        "route": "#C06443",
        "roads": "#8A8C94",
        "buildings": "#2F2E30",
        "water": "#8A8C94",
        "landscape": "#F4EFEB",
    },
    "nocturne": {
        "base": "#2F2E30",
        "route": "#C06443",
        "roads": "#485155",
        "buildings": "#BBADA4",
        "water": "#2F2E30",
        "landscape": "#2F2E30",
    },
    "ridgeline-sage": {
        "base": "#5F6244",
        "route": "#C06443",
        "roads": "#777E71",
        "buildings": "#E4D0B0",
        "water": "#5F6244",
        "landscape": "#5F6244",
    },
    "desert-archive": {
        "base": "#DBBAA5",
        "route": "#C06443",
        "roads": "#E4D0B0",
        "buildings": "#2F2E30",
        "water": "#DBBAA5",
        "landscape": "#DBBAA5",
    },
    "coastal-limestone": {
        "base": "#F4EFEB",
        "route": "#C06443",
        "roads": "#E4D0B0",
        "buildings": "#8A8C94",
        "water": "#5F778E",
        "landscape": "#F4EFEB",
    },
    "deco-after-dark": {
        "base": "#36364A",
        "route": "#C06443",
        "roads": "#BBADA4",
        "buildings": "#92864F",
        "water": "#36364A",
        "landscape": "#36364A",
    },
    "meridian-atlas": {
        "base": "#BBADA4",
        "route": "#C06443",
        "roads": "#E4D0B0",
        "buildings": "#485155",
        "water": "#5F778E",
        "landscape": "#BBADA4",
    },
    "vector-lab": {
        "base": "#2F2E30",
        "route": "#D7D602",
        "roads": "#485155",
        "buildings": "#F4EFEB",
        "water": "#2F2E30",
        "landscape": "#2F2E30",
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
