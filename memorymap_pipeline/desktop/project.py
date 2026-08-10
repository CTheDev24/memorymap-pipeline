from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from ..map_frame import MapFrame
from ..palettes import default_preset, resolve_palette
from ..route_markers import RouteMarkerMode


STYLE_PROFILE_URBAN = "urban"
STYLE_PROFILE_LANDSCAPE = "landscape"
STYLE_PROFILES = frozenset({STYLE_PROFILE_URBAN, STYLE_PROFILE_LANDSCAPE})


@dataclass(frozen=True)
class DesktopProject:
    """Serializable state needed to restore a desktop editing session."""

    frame: MapFrame
    gpx_path: str | None = None
    include_roads: bool = True
    include_buildings: bool = True
    include_terrain: bool = False
    include_water: bool = False
    flat_border_enabled: bool = False
    terrain_relief_mm: float = 3.0
    water_recess_mm: float = 0.4
    route_width_mm: float = 1.2
    route_height_mm: float | None = None
    route_markers: str = "none"
    route_layer_height_mm: float = 0.16
    style_profile: str = STYLE_PROFILE_URBAN
    surface_skin_thickness_mm: float = 0.4
    minimum_waterway_width_mm: float = 0.8
    ground_cover_mode: str = "auto"
    ground_cover_sensitivity: str = "balanced"
    color_preset: str | None = None
    layer_colors: dict[str, str] = field(default_factory=dict)
    version: int = field(default=2, init=False)

    def __post_init__(self) -> None:
        if self.style_profile not in STYLE_PROFILES:
            raise ValueError(
                f"Unsupported style profile: {self.style_profile!r}"
            )
        if min(
            self.route_width_mm,
            self.route_layer_height_mm,
            self.terrain_relief_mm,
            self.water_recess_mm,
            self.surface_skin_thickness_mm,
            self.minimum_waterway_width_mm,
        ) <= 0:
            raise ValueError("Route, terrain, and style dimensions must be positive")
        if self.route_height_mm is not None and self.route_height_mm <= 0:
            raise ValueError("Route, terrain, and style dimensions must be positive")
        RouteMarkerMode.parse(self.route_markers)
        if self.ground_cover_mode not in {"auto", "osm-only", "off"}:
            raise ValueError("Unsupported ground-cover mode")
        if self.ground_cover_sensitivity not in {"conservative", "balanced", "broad"}:
            raise ValueError("Unsupported ground-cover sensitivity")
        resolve_palette(self.style_profile, self.color_preset, self.layer_colors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "gpx_path": self.gpx_path,
            "frame": asdict(self.frame),
            "flat_border_enabled": self.flat_border_enabled,
            "layers": {
                "roads": self.include_roads,
                "buildings": self.include_buildings,
                "terrain": self.include_terrain,
                "water": self.include_water,
            },
            "route": {
                "width_mm": self.route_width_mm,
                "height_mm": self.route_height_mm,
                "layer_height_mm": self.route_layer_height_mm,
                "markers": self.route_markers,
            },
            "style": {
                "profile": self.style_profile,
                "surface_skin_thickness_mm": self.surface_skin_thickness_mm,
                "minimum_waterway_width_mm": self.minimum_waterway_width_mm,
                "ground_cover_mode": self.ground_cover_mode,
                "ground_cover_sensitivity": self.ground_cover_sensitivity,
                "color_preset": self.color_preset or default_preset(self.style_profile),
                "layer_colors": dict(self.layer_colors),
            },
            "terrain": {
                "relief_mm": self.terrain_relief_mm,
                "water_recess_mm": self.water_recess_mm,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, allow_nan=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DesktopProject":
        version = value.get("version")
        if version not in {1, 2}:
            raise ValueError("Unsupported desktop project version")
        try:
            layers = value.get("layers", {})
            route = value.get("route", {})
            style = value.get("style", {})
            terrain = value.get("terrain", {})
            return cls(
                frame=MapFrame(**value["frame"]),
                gpx_path=value.get("gpx_path"),
                include_roads=bool(layers.get("roads", True)),
                include_buildings=bool(layers.get("buildings", True)),
                include_terrain=bool(layers.get("terrain", False)),
                include_water=bool(layers.get("water", False)),
                flat_border_enabled=bool(
                    value.get("flat_border_enabled", False)
                ),
                terrain_relief_mm=float(terrain.get("relief_mm", 3.0)),
                water_recess_mm=float(terrain.get("water_recess_mm", 0.4)),
                route_width_mm=float(route.get("width_mm", 1.2)),
                route_height_mm=(
                    float(
                        2.0
                        if route.get("height_mm") is None
                        else route["height_mm"]
                    )
                    if version == 1
                    else (
                        None
                        if route.get("height_mm") is None
                        else float(route["height_mm"])
                    )
                ),
                route_layer_height_mm=float(route.get("layer_height_mm", 0.16)),
                route_markers=(
                    "none" if version == 1 else str(route.get("markers", "none"))
                ),
                style_profile=str(style.get("profile", STYLE_PROFILE_URBAN)),
                surface_skin_thickness_mm=float(
                    style.get("surface_skin_thickness_mm", 0.4)
                ),
                minimum_waterway_width_mm=float(
                    style.get("minimum_waterway_width_mm", 0.8)
                ),
                ground_cover_mode=str(style.get("ground_cover_mode", "auto")),
                ground_cover_sensitivity=str(
                    style.get("ground_cover_sensitivity", "balanced")
                ),
                color_preset=style.get("color_preset"),
                layer_colors=dict(style.get("layer_colors", {})),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid desktop project: {exc}") from exc

    @classmethod
    def from_json(cls, value: str) -> "DesktopProject":
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid desktop project JSON: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise ValueError("Desktop project must be a JSON object")
        return cls.from_dict(data)

    def save(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.to_json() + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "DesktopProject":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))
