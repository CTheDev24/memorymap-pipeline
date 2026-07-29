from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from ..map_frame import MapFrame


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
    route_height_mm: float = 2.0
    style_profile: str = STYLE_PROFILE_URBAN
    surface_skin_thickness_mm: float = 0.4
    minimum_waterway_width_mm: float = 0.8
    version: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        if self.style_profile not in STYLE_PROFILES:
            raise ValueError(
                f"Unsupported style profile: {self.style_profile!r}"
            )
        if min(
            self.route_width_mm,
            self.route_height_mm,
            self.terrain_relief_mm,
            self.water_recess_mm,
            self.surface_skin_thickness_mm,
            self.minimum_waterway_width_mm,
        ) <= 0:
            raise ValueError("Route, terrain, and style dimensions must be positive")

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
            "route": {"width_mm": self.route_width_mm, "height_mm": self.route_height_mm},
            "style": {
                "profile": self.style_profile,
                "surface_skin_thickness_mm": self.surface_skin_thickness_mm,
                "minimum_waterway_width_mm": self.minimum_waterway_width_mm,
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
        if value.get("version") != 1:
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
                route_height_mm=float(route.get("height_mm", 2.0)),
                style_profile=str(style.get("profile", STYLE_PROFILE_URBAN)),
                surface_skin_thickness_mm=float(
                    style.get("surface_skin_thickness_mm", 0.4)
                ),
                minimum_waterway_width_mm=float(
                    style.get("minimum_waterway_width_mm", 0.8)
                ),
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
