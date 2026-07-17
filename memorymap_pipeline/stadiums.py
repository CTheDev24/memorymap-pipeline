"""Print-safe procedural stadium geometry.

The primitives in this module deliberately describe stadium *massing*, not facade
detail.  A data-driven landmark recipe can supply surveyed footprints later while
the same builder remains useful for stadiums in any city.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from trimesh import Trimesh
from trimesh.creation import extrude_polygon
from trimesh.util import concatenate


RoofStyle = Literal["none", "asymmetric", "retractable", "closed"]
RoofSide = Literal["west", "east"]


@dataclass(frozen=True)
class StadiumRecipe:
    """Scale-dependent parameters for a recognizable, printable stadium mass.

    All dimensions are model millimetres.  ``roof_coverage`` is the fraction of the
    field span covered by roof panels: one panel for ``asymmetric`` and split evenly
    between two panels for ``retractable``.
    """

    bowl_height_mm: float = 4.0
    tier_count: int = 3
    minimum_feature_mm: float = 0.8
    roof_style: RoofStyle = "none"
    roof_height_mm: float = 6.0
    roof_thickness_mm: float = 0.8
    roof_coverage: float = 0.38
    roof_orientation_degrees: float = 0.0
    roof_side: RoofSide = "west"
    roof_support_width_mm: float = 1.2
    support_overlap_mm: float = 0.15
    closed_roof_band_count: int = 3
    closed_roof_band_width_mm: float = 1.2
    closed_roof_band_height_mm: float = 0.48

    def validate(self) -> None:
        if self.minimum_feature_mm < 0.8:
            raise ValueError("Stadium minimum_feature_mm must be at least 0.8 mm")
        if self.bowl_height_mm <= 0.0:
            raise ValueError("Stadium bowl height must be positive")
        if self.tier_count < 1:
            raise ValueError("Stadium tier_count must be at least one")
        if self.roof_style not in ("none", "asymmetric", "retractable", "closed"):
            raise ValueError(f"Unsupported stadium roof style: {self.roof_style}")
        if self.roof_style != "none":
            if self.roof_thickness_mm < self.minimum_feature_mm:
                raise ValueError("Stadium roof thickness is below the minimum feature size")
            if self.roof_support_width_mm < self.minimum_feature_mm:
                raise ValueError("Stadium roof support is below the minimum feature size")
            if self.roof_height_mm <= self.bowl_height_mm:
                raise ValueError("Stadium roof must be higher than the bowl")
            if self.roof_style != "closed" and not 0.05 <= self.roof_coverage <= 0.9:
                raise ValueError("Stadium roof coverage must be between 0.05 and 0.9")
            if not 0.0 < self.support_overlap_mm < self.roof_thickness_mm:
                raise ValueError("Stadium roof support overlap must be positive and thin")
            if self.roof_style == "closed":
                if self.closed_roof_band_count not in (2, 3):
                    raise ValueError("Closed stadium roofs require two or three broad bands")
                if self.closed_roof_band_width_mm < self.minimum_feature_mm:
                    raise ValueError(
                        "Closed stadium roof band width is below the minimum feature size"
                    )
                if self.closed_roof_band_height_mm <= 0.0:
                    raise ValueError("Closed stadium roof band height must be positive")


def _polygons(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    return [part for part in geometry.geoms if isinstance(part, Polygon)]


def _extrude_geometry(geometry: BaseGeometry, height: float, z_bottom: float = 0.0) -> list[Trimesh]:
    meshes: list[Trimesh] = []
    for polygon in _polygons(geometry):
        if polygon.area <= 1e-8:
            continue
        mesh = extrude_polygon(polygon, height)
        if z_bottom:
            mesh.apply_translation((0.0, 0.0, z_bottom))
        meshes.append(mesh)
    return meshes


def _normalise_footprints(
    outer: Polygon,
    inner: Polygon | None,
    minimum_feature_mm: float,
) -> tuple[Polygon, Polygon]:
    outer = outer.buffer(0)
    if not isinstance(outer, Polygon) or outer.is_empty:
        raise ValueError("Stadium outer footprint must be one valid polygon")

    if inner is None:
        inset = max(minimum_feature_mm * 3.0, min(outer.bounds[2] - outer.bounds[0], outer.bounds[3] - outer.bounds[1]) * 0.2)
        # Mitred offsets avoid sub-nozzle arc facets and a known earcut ambiguity at
        # tangent points in rounded rectangular rings.
        inner = outer.buffer(-inset, join_style="mitre")
    else:
        inner = inner.buffer(0)
    if not isinstance(inner, Polygon) or inner.is_empty:
        raise ValueError("Stadium inner opening must be one valid polygon")
    if not outer.covers(inner.buffer(minimum_feature_mm - 1e-6)):
        raise ValueError(
            "Stadium opening must leave at least minimum_feature_mm of bowl on every side"
        )
    return outer, inner


def _tier_regions(outer: Polygon, inner: Polygon, recipe: StadiumRecipe) -> list[BaseGeometry]:
    clearance = outer.exterior.distance(inner.exterior)
    required = recipe.tier_count * recipe.minimum_feature_mm
    if clearance + 1e-6 < required:
        raise ValueError(
            f"Stadium footprint has {clearance:.2f} mm bowl clearance; "
            f"{required:.2f} mm is required for {recipe.tier_count} printable tiers"
        )

    step = clearance / recipe.tier_count
    regions: list[BaseGeometry] = []
    previous = inner
    for index in range(recipe.tier_count):
        if index == recipe.tier_count - 1:
            expanded = outer
        else:
            expanded = inner.buffer(
                (index + 1) * step, join_style="mitre"
            ).intersection(outer)
        regions.append(expanded.difference(previous).buffer(0))
        previous = expanded
    return regions


def _oriented_roof_parts(
    outer: Polygon,
    inner: Polygon,
    recipe: StadiumRecipe,
) -> tuple[list[BaseGeometry], list[BaseGeometry]]:
    """Return roof-panel plans and their ground-connected support-wall plans."""
    origin = outer.centroid.coords[0]
    angle = -recipe.roof_orientation_degrees
    rotated_outer = affinity.rotate(outer, angle, origin=origin)
    rotated_inner = affinity.rotate(inner, angle, origin=origin)
    min_x, min_y, max_x, max_y = rotated_inner.bounds
    span = max_x - min_x
    pad = recipe.minimum_feature_mm
    support = recipe.roof_support_width_mm

    sides = [recipe.roof_side]
    cover_each = recipe.roof_coverage
    if recipe.roof_style == "retractable":
        sides = ["west", "east"]
        cover_each /= 2.0

    panels: list[BaseGeometry] = []
    supports: list[BaseGeometry] = []
    bowl = rotated_outer.difference(rotated_inner)
    for side in sides:
        if side == "west":
            edge = min_x
            panel_rect = box(edge - support, min_y - pad, edge + span * cover_each, max_y + pad)
            support_rect = box(edge - support, min_y - pad, edge + recipe.support_overlap_mm, max_y + pad)
        else:
            edge = max_x
            panel_rect = box(edge - span * cover_each, min_y - pad, edge + support, max_y + pad)
            support_rect = box(edge - recipe.support_overlap_mm, min_y - pad, edge + support, max_y + pad)

        # Panel overlap into the bowl provides a load path; the support itself never
        # becomes a floating decorative shell.
        panel = panel_rect.intersection(rotated_outer).buffer(0)
        wall = support_rect.intersection(bowl).buffer(0)
        panels.append(affinity.rotate(panel, -angle, origin=origin))
        supports.append(affinity.rotate(wall, -angle, origin=origin))
    return panels, supports


def _closed_roof_bands(outer: Polygon, recipe: StadiumRecipe) -> list[BaseGeometry]:
    """Create broad, shallow ribs clipped to the closed-roof silhouette."""
    origin = outer.centroid.coords[0]
    angle = -recipe.roof_orientation_degrees
    rotated_outer = affinity.rotate(outer, angle, origin=origin)
    min_x, min_y, max_x, max_y = rotated_outer.bounds
    width = recipe.closed_roof_band_width_mm
    pad = recipe.minimum_feature_mm

    bands: list[BaseGeometry] = []
    for index in range(1, recipe.closed_roof_band_count + 1):
        center_x = min_x + (max_x - min_x) * index / (recipe.closed_roof_band_count + 1)
        strip = box(center_x - width / 2.0, min_y - pad, center_x + width / 2.0, max_y + pad)
        clipped = strip.intersection(rotated_outer).buffer(0)
        bands.append(affinity.rotate(clipped, -angle, origin=origin))
    return bands


def build_stadium_mesh(
    outer_footprint: Polygon,
    *,
    inner_opening: Polygon | None = None,
    recipe: StadiumRecipe | None = None,
) -> Trimesh:
    """Build a city-agnostic stadium mass from print-coordinate footprints.

    The returned mesh may contain several watertight bodies, but every body starts at
    the ground plane or overlaps a ground-connected roof support.  This is intentional:
    slicers union the same-material bodies, while retaining simple, repairable topology.
    """
    recipe = recipe or StadiumRecipe()
    recipe.validate()
    outer, inner = _normalise_footprints(
        outer_footprint, inner_opening, recipe.minimum_feature_mm
    )

    meshes: list[Trimesh] = []
    if recipe.roof_style == "closed":
        # A solid mass is intentional at print scale: it follows the stadium outline
        # while avoiding a field-width bridge beneath the closed roof.
        meshes.extend(_extrude_geometry(outer, recipe.roof_height_mm))
        rib_bottom = recipe.roof_height_mm - recipe.support_overlap_mm
        rib_height = recipe.closed_roof_band_height_mm + recipe.support_overlap_mm
        for band in _closed_roof_bands(outer, recipe):
            meshes.extend(_extrude_geometry(band, rib_height, rib_bottom))
    else:
        for index, region in enumerate(_tier_regions(outer, inner, recipe), start=1):
            height = recipe.bowl_height_mm * index / recipe.tier_count
            meshes.extend(_extrude_geometry(region, height))

    if recipe.roof_style in ("asymmetric", "retractable"):
        panels, supports = _oriented_roof_parts(outer, inner, recipe)
        roof_bottom = recipe.roof_height_mm - recipe.roof_thickness_mm
        support_height = roof_bottom + recipe.support_overlap_mm
        for wall in supports:
            meshes.extend(_extrude_geometry(wall, support_height))
        for panel in panels:
            meshes.extend(_extrude_geometry(panel, recipe.roof_thickness_mm, roof_bottom))

    if not meshes:
        raise ValueError("Stadium recipe produced no printable geometry")
    result = concatenate(meshes)
    result.metadata["stadium_recipe"] = {
        "roof_style": recipe.roof_style,
        "tier_count": recipe.tier_count,
        "minimum_feature_mm": recipe.minimum_feature_mm,
    }
    return result


__all__ = ["RoofSide", "RoofStyle", "StadiumRecipe", "build_stadium_mesh"]
