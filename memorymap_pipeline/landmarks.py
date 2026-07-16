"""Offline landmark definitions keyed by stable external identifiers.

The registry deliberately contains no geographic or city-specific matching logic.
Callers identify a landmark using an OSM element ID or Wikidata item and may then
apply its tag corrections and optional geometry enhancement.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
import json
import re
from typing import Any, Mapping


SCHEMA_VERSION = 1
_ELEMENT_TYPES = frozenset({"node", "way", "relation"})
_WIKIDATA_RE = re.compile(r"^Q[1-9][0-9]*$")
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class LandmarkRegistryError(ValueError):
    """Raised when a landmark registry is malformed or contains ambiguous IDs."""


@dataclass(frozen=True)
class LandmarkEnhancement:
    """A print-safe geometry enhancement associated with a landmark."""

    kind: str
    reference: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class LandmarkDefinition:
    """One versioned landmark definition from the bundled registry."""

    key: str
    display_name: str
    aliases: tuple[str, ...]
    wikidata_ids: tuple[str, ...]
    osm_ids: Mapping[str, tuple[int, ...]]
    tag_corrections: Mapping[str, str | int | float | bool | None]
    enhancement: LandmarkEnhancement | None


class LandmarkRegistry:
    """Validated in-memory index of offline landmark definitions."""

    def __init__(
        self,
        *,
        registry_version: str,
        landmarks: tuple[LandmarkDefinition, ...],
    ) -> None:
        self.registry_version = registry_version
        self.landmarks = landmarks
        self._by_key: dict[str, LandmarkDefinition] = {}
        self._by_wikidata: dict[str, LandmarkDefinition] = {}
        self._by_osm: dict[tuple[str, int], LandmarkDefinition] = {}

        for landmark in landmarks:
            self._index_unique(self._by_key, landmark.key, landmark, "landmark key")
            for wikidata_id in landmark.wikidata_ids:
                self._index_unique(
                    self._by_wikidata, wikidata_id, landmark, "Wikidata identifier"
                )
            for element_type, element_ids in landmark.osm_ids.items():
                for element_id in element_ids:
                    self._index_unique(
                        self._by_osm,
                        (element_type, element_id),
                        landmark,
                        "OSM identifier",
                    )

    @staticmethod
    def _index_unique(index: dict, identifier: Any, landmark: LandmarkDefinition, label: str) -> None:
        existing = index.get(identifier)
        if existing is not None and existing.key != landmark.key:
            raise LandmarkRegistryError(
                f"Duplicate {label} {identifier!r} in {existing.key!r} and {landmark.key!r}"
            )
        index[identifier] = landmark

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "LandmarkRegistry":
        """Validate and load a registry document."""

        if not isinstance(document, Mapping):
            raise LandmarkRegistryError("Registry root must be an object")
        if document.get("schema_version") != SCHEMA_VERSION:
            raise LandmarkRegistryError(
                f"Unsupported landmark schema version {document.get('schema_version')!r}; "
                f"expected {SCHEMA_VERSION}"
            )
        registry_version = _required_string(document, "registry_version", "registry")
        raw_landmarks = document.get("landmarks")
        if not isinstance(raw_landmarks, list):
            raise LandmarkRegistryError("Registry 'landmarks' must be an array")

        landmarks = tuple(_parse_landmark(item, index) for index, item in enumerate(raw_landmarks))
        return cls(registry_version=registry_version, landmarks=landmarks)

    @classmethod
    def from_json(cls, text: str) -> "LandmarkRegistry":
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LandmarkRegistryError(f"Invalid landmark registry JSON: {exc}") from exc
        return cls.from_dict(document)

    def get(self, key: str) -> LandmarkDefinition | None:
        """Return a definition by its internal key, or ``None`` when absent."""

        return self._by_key.get(key)

    def match(
        self,
        *,
        wikidata: str | None = None,
        osm_type: str | None = None,
        osm_id: int | str | None = None,
    ) -> LandmarkDefinition | None:
        """Match stable identifiers, returning ``None`` when none are known.

        Supplying identifiers that resolve to different landmarks is treated as a
        data error instead of silently choosing one.
        """

        matches: dict[str, LandmarkDefinition] = {}
        if wikidata:
            for item_id in _split_wikidata(wikidata):
                landmark = self._by_wikidata.get(item_id)
                if landmark is not None:
                    matches[landmark.key] = landmark
        if osm_type is not None or osm_id is not None:
            element_type, element_id = _normalise_osm_identifier(osm_type, osm_id)
            landmark = self._by_osm.get((element_type, element_id))
            if landmark is not None:
                matches[landmark.key] = landmark

        if len(matches) > 1:
            raise LandmarkRegistryError(
                "Provided stable identifiers resolve to different landmark definitions"
            )
        return next(iter(matches.values()), None)

    def match_feature(
        self,
        tags: Mapping[str, Any],
        *,
        osm_type: str | None = None,
        osm_id: int | str | None = None,
    ) -> LandmarkDefinition | None:
        """Match an OSM-like feature without relying on its mutable name."""

        wikidata = tags.get("wikidata") if isinstance(tags, Mapping) else None
        return self.match(
            wikidata=str(wikidata) if wikidata else None,
            osm_type=osm_type,
            osm_id=osm_id,
        )


@lru_cache(maxsize=1)
def load_default_landmark_registry() -> LandmarkRegistry:
    """Load the versioned registry bundled with :mod:`memorymap_pipeline`."""

    resource = resources.files("memorymap_pipeline").joinpath("data", "landmarks.v1.json")
    try:
        return LandmarkRegistry.from_json(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as exc:
        raise LandmarkRegistryError(f"Bundled landmark registry is unavailable: {exc}") from exc


def _parse_landmark(raw: Any, index: int) -> LandmarkDefinition:
    context = f"landmarks[{index}]"
    if not isinstance(raw, Mapping):
        raise LandmarkRegistryError(f"{context} must be an object")
    key = _required_string(raw, "key", context)
    if not _TOKEN_RE.fullmatch(key):
        raise LandmarkRegistryError(f"{context}.key must be a lowercase stable token")
    display_name = _required_string(raw, "display_name", context)
    aliases = _string_array(raw.get("aliases", []), f"{context}.aliases")

    identifiers = raw.get("identifiers")
    if not isinstance(identifiers, Mapping):
        raise LandmarkRegistryError(f"{context}.identifiers must be an object")
    wikidata_ids = _string_array(
        identifiers.get("wikidata", []), f"{context}.identifiers.wikidata"
    )
    for item_id in wikidata_ids:
        if not _WIKIDATA_RE.fullmatch(item_id):
            raise LandmarkRegistryError(f"Invalid Wikidata identifier {item_id!r} in {context}")

    raw_osm = identifiers.get("osm", {})
    if not isinstance(raw_osm, Mapping):
        raise LandmarkRegistryError(f"{context}.identifiers.osm must be an object")
    osm_ids: dict[str, tuple[int, ...]] = {}
    for element_type, raw_ids in raw_osm.items():
        if element_type not in _ELEMENT_TYPES:
            raise LandmarkRegistryError(f"Invalid OSM element type {element_type!r} in {context}")
        if not isinstance(raw_ids, list):
            raise LandmarkRegistryError(f"OSM {element_type} IDs in {context} must be an array")
        ids = tuple(_positive_integer(value, f"{context}.identifiers.osm.{element_type}") for value in raw_ids)
        osm_ids[element_type] = ids
    if not wikidata_ids and not any(osm_ids.values()):
        raise LandmarkRegistryError(f"{context} must contain a Wikidata or OSM identifier")

    raw_corrections = raw.get("tag_corrections", {})
    if not isinstance(raw_corrections, Mapping):
        raise LandmarkRegistryError(f"{context}.tag_corrections must be an object")
    corrections: dict[str, str | int | float | bool | None] = {}
    for tag, value in raw_corrections.items():
        if not isinstance(tag, str) or not tag.strip():
            raise LandmarkRegistryError(f"Tag correction keys in {context} must be strings")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise LandmarkRegistryError(f"Tag correction {tag!r} in {context} must be scalar")
        corrections[tag] = value

    enhancement = _parse_enhancement(raw.get("enhancement"), context)
    return LandmarkDefinition(
        key=key,
        display_name=display_name,
        aliases=aliases,
        wikidata_ids=wikidata_ids,
        osm_ids=osm_ids,
        tag_corrections=corrections,
        enhancement=enhancement,
    )


def _parse_enhancement(raw: Any, context: str) -> LandmarkEnhancement | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise LandmarkRegistryError(f"{context}.enhancement must be an object")
    kind = raw.get("type")
    if kind == "procedural_recipe":
        reference = _required_string(raw, "recipe", f"{context}.enhancement")
        metadata = raw.get("parameters", {})
    elif kind == "curated_mesh":
        reference = _required_string(raw, "resource", f"{context}.enhancement")
        if reference.startswith(("/", "\\")) or ".." in reference.replace("\\", "/").split("/"):
            raise LandmarkRegistryError(f"{context}.enhancement.resource must be package-relative")
        metadata = {key: value for key, value in raw.items() if key not in {"type", "resource"}}
    else:
        raise LandmarkRegistryError(
            f"{context}.enhancement.type must be 'procedural_recipe' or 'curated_mesh'"
        )
    if not _TOKEN_RE.fullmatch(reference) and kind == "procedural_recipe":
        raise LandmarkRegistryError(f"{context}.enhancement.recipe must be a stable token")
    if not isinstance(metadata, Mapping):
        raise LandmarkRegistryError(f"{context}.enhancement metadata must be an object")
    return LandmarkEnhancement(kind=kind, reference=reference, metadata=dict(metadata))


def _required_string(raw: Mapping[str, Any], key: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LandmarkRegistryError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def _string_array(raw: Any, context: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or any(not isinstance(value, str) or not value.strip() for value in raw):
        raise LandmarkRegistryError(f"{context} must be an array of non-empty strings")
    return tuple(value.strip() for value in raw)


def _positive_integer(raw: Any, context: str) -> int:
    if isinstance(raw, bool):
        raise LandmarkRegistryError(f"{context} values must be positive integers")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise LandmarkRegistryError(f"{context} values must be positive integers") from exc
    if value <= 0 or str(raw).strip() != str(value):
        raise LandmarkRegistryError(f"{context} values must be positive integers")
    return value


def _normalise_osm_identifier(
    osm_type: str | None, osm_id: int | str | None
) -> tuple[str, int]:
    if osm_type not in _ELEMENT_TYPES or osm_id is None:
        raise LandmarkRegistryError("Both a valid OSM element type and ID are required")
    return osm_type, _positive_integer(osm_id, "OSM ID")


def _split_wikidata(raw: str) -> tuple[str, ...]:
    values = tuple(value.strip() for value in raw.split(";") if value.strip())
    for value in values:
        if not _WIKIDATA_RE.fullmatch(value):
            raise LandmarkRegistryError(f"Invalid Wikidata identifier {value!r}")
    return values
