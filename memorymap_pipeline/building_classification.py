"""City-agnostic building classification and print-aware defaults.

The classifier consumes mappings shaped like OpenStreetMap tags.  It does not
replace explicit height or roof data: callers should use these presets only
when source data is missing, or to choose an appropriate simplification
strategy for the current print scale.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class BuildingClass(str, Enum):
    """Broad building uses that have meaning at desktop-print scale."""

    RESIDENTIAL = "residential"
    COMMERCIAL_OFFICE = "commercial_office"
    INDUSTRIAL_WAREHOUSE = "industrial_warehouse"
    RETAIL = "retail"
    PARKING = "parking"
    CIVIC_INSTITUTIONAL = "civic_institutional"
    STADIUM_ARENA = "stadium_arena"
    RELIGIOUS = "religious"
    LANDMARK = "landmark"
    UNKNOWN = "unknown"


class DetailBehavior(str, Enum):
    """How aggressively downstream mesh generation may simplify geometry."""

    SIMPLIFY = "simplify"
    STANDARD = "standard"
    PRESERVE = "preserve"


@dataclass(frozen=True)
class BuildingPreset:
    """Fallback dimensions and print policy for a building class.

    ``fallback_levels`` and ``floor_height_m`` estimate real-world height only
    when explicit OSM height/level data is unavailable.  The millimetre values
    apply after map scaling and make the resulting feature printable.
    """

    fallback_levels: float
    floor_height_m: float
    min_printable_height_mm: float
    max_visual_height_mm: float
    roof_preferences: tuple[str, ...]
    detail_behavior: DetailBehavior
    merge_building_parts: bool = True

    @property
    def fallback_height_m(self) -> float:
        return self.fallback_levels * self.floor_height_m


# These defaults intentionally describe recognizable, printable massing rather
# than architectural truth. Explicit source tags and landmark recipes take
# precedence in the generation pipeline.
BUILDING_PRESETS: Mapping[BuildingClass, BuildingPreset] = MappingProxyType(
    {
        BuildingClass.RESIDENTIAL: BuildingPreset(
            2.0, 3.0, 0.8, 14.0, ("flat", "gabled", "hipped"), DetailBehavior.STANDARD
        ),
        BuildingClass.COMMERCIAL_OFFICE: BuildingPreset(
            6.0, 3.6, 0.8, 31.8, ("flat", "pyramidal", "hipped"), DetailBehavior.PRESERVE
        ),
        BuildingClass.INDUSTRIAL_WAREHOUSE: BuildingPreset(
            1.0, 6.0, 0.8, 12.0, ("flat", "gabled", "skillion"), DetailBehavior.SIMPLIFY
        ),
        BuildingClass.RETAIL: BuildingPreset(
            1.0, 4.5, 0.8, 12.0, ("flat", "gabled"), DetailBehavior.SIMPLIFY
        ),
        BuildingClass.PARKING: BuildingPreset(
            4.0, 3.0, 0.8, 16.0, ("flat",), DetailBehavior.SIMPLIFY
        ),
        BuildingClass.CIVIC_INSTITUTIONAL: BuildingPreset(
            3.0, 3.8, 0.8, 22.0, ("flat", "gabled", "hipped"), DetailBehavior.PRESERVE
        ),
        BuildingClass.STADIUM_ARENA: BuildingPreset(
            3.0,
            6.0,
            1.0,
            26.0,
            ("flat", "dome", "barrel", "retractable"),
            DetailBehavior.PRESERVE,
            merge_building_parts=False,
        ),
        BuildingClass.RELIGIOUS: BuildingPreset(
            2.0,
            4.5,
            0.8,
            26.0,
            ("gabled", "hipped", "pyramidal", "dome"),
            DetailBehavior.PRESERVE,
            merge_building_parts=False,
        ),
        BuildingClass.LANDMARK: BuildingPreset(
            8.0,
            4.0,
            1.0,
            31.8,
            ("flat", "gabled", "hipped", "pyramidal", "dome"),
            DetailBehavior.PRESERVE,
            merge_building_parts=False,
        ),
        BuildingClass.UNKNOWN: BuildingPreset(
            2.0, 3.0, 0.8, 18.0, ("flat",), DetailBehavior.STANDARD
        ),
    }
)


_CLASS_ALIASES: Mapping[str, BuildingClass] = MappingProxyType(
    {
        "residential": BuildingClass.RESIDENTIAL,
        "commercial": BuildingClass.COMMERCIAL_OFFICE,
        "office": BuildingClass.COMMERCIAL_OFFICE,
        "commercial_office": BuildingClass.COMMERCIAL_OFFICE,
        "industrial": BuildingClass.INDUSTRIAL_WAREHOUSE,
        "warehouse": BuildingClass.INDUSTRIAL_WAREHOUSE,
        "industrial_warehouse": BuildingClass.INDUSTRIAL_WAREHOUSE,
        "retail": BuildingClass.RETAIL,
        "parking": BuildingClass.PARKING,
        "civic": BuildingClass.CIVIC_INSTITUTIONAL,
        "institutional": BuildingClass.CIVIC_INSTITUTIONAL,
        "civic_institutional": BuildingClass.CIVIC_INSTITUTIONAL,
        "stadium": BuildingClass.STADIUM_ARENA,
        "arena": BuildingClass.STADIUM_ARENA,
        "stadium_arena": BuildingClass.STADIUM_ARENA,
        "religious": BuildingClass.RELIGIOUS,
        "landmark": BuildingClass.LANDMARK,
        "unknown": BuildingClass.UNKNOWN,
    }
)

_RESIDENTIAL = {
    "apartments", "bungalow", "cabin", "detached", "dormitory", "house",
    "residential", "semidetached_house", "static_caravan", "terrace",
}
_COMMERCIAL = {"commercial", "office", "offices"}
_INDUSTRIAL = {"factory", "hangar", "industrial", "manufacture", "warehouse"}
_RETAIL = {"kiosk", "retail", "shop", "supermarket"}
_PARKING = {"carport", "garage", "garages", "parking", "parking_garage"}
_CIVIC_BUILDINGS = {
    "civic", "college", "government", "hospital", "kindergarten", "public",
    "school", "train_station", "transportation", "university",
}
_CIVIC_AMENITIES = {
    "arts_centre", "clinic", "college", "community_centre", "courthouse",
    "fire_station", "hospital", "kindergarten", "library", "police",
    "school", "social_facility", "townhall", "university",
}
_RELIGIOUS = {
    "cathedral", "chapel", "church", "kingdom_hall", "monastery", "mosque",
    "religious", "shrine", "synagogue", "temple",
}


def _tokens(tags: Mapping[str, object], key: str) -> set[str]:
    value = tags.get(key)
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set, frozenset)):
        raw_values = value
    else:
        raw_values = (value,)
    return {
        token.strip().lower().replace("-", "_").replace(" ", "_")
        for raw in raw_values
        for token in str(raw).split(";")
        if token.strip()
    }


def classify_building(tags: Mapping[str, object]) -> BuildingClass:
    """Classify OSM-like tags using a stable, documented priority order.

    Priority is: explicit MemoryMap override, landmark, stadium, religious,
    parking, civic, retail, industrial, commercial, residential, unknown.
    Higher-significance uses therefore win when mixed-use tags conflict.
    """

    override = _tokens(tags, "memorymap:class")
    if override:
        return _CLASS_ALIASES.get(sorted(override)[0], BuildingClass.UNKNOWN)

    building = _tokens(tags, "building") | _tokens(tags, "building:part")
    amenity = _tokens(tags, "amenity")
    leisure = _tokens(tags, "leisure")
    landuse = _tokens(tags, "landuse")

    if (
        _tokens(tags, "landmark") & {"yes", "true", "1"}
        or (
            _tokens(tags, "tourism") & {"attraction"}
            and (_tokens(tags, "wikidata") or _tokens(tags, "wikipedia"))
        )
    ):
        return BuildingClass.LANDMARK
    if building & {"arena", "grandstand", "pavilion", "stadium", "sports_hall"} or leisure & {
        "stadium", "sports_centre"
    }:
        return BuildingClass.STADIUM_ARENA
    if building & _RELIGIOUS or amenity & {"place_of_worship", "monastery"}:
        return BuildingClass.RELIGIOUS
    if building & _PARKING or amenity & {"parking", "parking_entrance"}:
        return BuildingClass.PARKING
    if building & _CIVIC_BUILDINGS or amenity & _CIVIC_AMENITIES:
        return BuildingClass.CIVIC_INSTITUTIONAL
    if building & _RETAIL or _tokens(tags, "shop") - {"no", "vacant"}:
        return BuildingClass.RETAIL
    if building & _INDUSTRIAL or landuse & {"industrial", "port"}:
        return BuildingClass.INDUSTRIAL_WAREHOUSE
    if building & _COMMERCIAL or _tokens(tags, "office") - {"no"}:
        return BuildingClass.COMMERCIAL_OFFICE
    if building & _RESIDENTIAL:
        return BuildingClass.RESIDENTIAL
    return BuildingClass.UNKNOWN


def preset_for(tags_or_class: Mapping[str, object] | BuildingClass) -> BuildingPreset:
    """Return the immutable preset for tags or an already resolved class."""

    category = (
        tags_or_class
        if isinstance(tags_or_class, BuildingClass)
        else classify_building(tags_or_class)
    )
    return BUILDING_PRESETS[category]
