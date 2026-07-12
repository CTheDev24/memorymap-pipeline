from __future__ import annotations

import logging
from typing import Iterable

import numpy as np
import shapely.geometry as geom
import shapely.ops as ops

from .geometry import repair_polygon
from .mesh import route_mesh_from_polygon
from .projection import apply_transform, project_lonlat_array


def _transform_shapely_polygon(
    polygon: geom.Polygon,
    center_lat: float,
    center_lon: float,
    transform: dict,
) -> geom.Polygon:
    """Apply lon/lat -> mm transform to all coordinates of a Shapely Polygon.

    Preserves all original vertices and ring order.
    """
    if polygon.is_empty:
        return polygon

    def _transform_ring(coords):
        coords = np.array(coords)
        if coords.size == 0:
            return coords
        lons = coords[:, 0]
        lats = coords[:, 1]
        projected = project_lonlat_array(lats, lons, center_lat=center_lat, center_lon=center_lon)
        frame = transform.get("map_frame")
        transformed = frame.transform_projected(projected) if frame is not None else apply_transform(projected, transform)
        return [(float(x), float(y)) for x, y in transformed]

    exterior = _transform_ring(polygon.exterior.coords)
    interiors = [_transform_ring(r.coords) for r in polygon.interiors]
    try:
        return geom.Polygon(exterior, interiors)
    except Exception:
        return polygon


def _extract_real_height_m(
    tags: dict,
    default_height_m: float,
    levels_to_m: float,
    max_height_m: float,
) -> float:
    """Extract real-world building height in metres from OSM tag dict.

    Priority:
    1. ``height`` tag (explicit metres; values tagged in feet are converted)
    2. ``building:levels`` × ``levels_to_m``
    3. ``default_height_m`` fallback

    Values above ``max_height_m`` are treated as corrupt and skipped.
    """
    height_val = tags.get("height")
    if height_val is not None:
        if isinstance(height_val, (list, tuple)):
            height_val = height_val[0] if height_val else None
        if height_val is not None:
            try:
                raw = str(height_val).strip()
                if raw.endswith("ft"):
                    # Convert feet to metres
                    h = float(raw[:-2].strip()) * 0.3048
                else:
                    h = float(raw.replace("m", "").strip())
                if 0.0 < h <= max_height_m:
                    return h
            except (ValueError, TypeError):
                pass

    levels_val = tags.get("building:levels")
    if levels_val is not None:
        if isinstance(levels_val, (list, tuple)):
            levels_val = levels_val[0] if levels_val else None
        if levels_val is not None:
            try:
                levels = float(str(levels_val).strip())
                max_levels = max_height_m / levels_to_m
                if 0.0 < levels <= max_levels:
                    return levels * levels_to_m
            except (ValueError, TypeError):
                pass

    roof_levels_val = tags.get("roof:levels")
    if roof_levels_val is not None:
        if isinstance(roof_levels_val, (list, tuple)):
            roof_levels_val = roof_levels_val[0] if roof_levels_val else None
        try:
            roof_levels = float(str(roof_levels_val).strip())
            estimated_height = default_height_m + roof_levels * levels_to_m
            if 0.0 < roof_levels and estimated_height <= max_height_m:
                return estimated_height
        except (ValueError, TypeError):
            pass

    building_type = tags.get("building")
    if isinstance(building_type, (list, tuple)):
        building_type = building_type[0] if building_type else None
    type_defaults = {
        "apartments": 12.0,
        "office": 12.0,
        "commercial": 9.0,
        "hotel": 12.0,
        "hospital": 12.0,
        "industrial": 8.0,
        "warehouse": 8.0,
        "retail": 6.0,
        "church": 12.0,
        "cathedral": 18.0,
        "garage": 3.0,
        "garages": 3.0,
        "shed": 3.0,
    }
    if building_type is not None:
        estimated = type_defaults.get(str(building_type).strip().lower())
        if estimated is not None:
            return min(estimated, max_height_m)

    return default_height_m


def _tags_from_gdf_row(row, columns: list[str]) -> dict:
    """Build a plain tag dict from a GeoDataFrame row, dropping missing values."""
    import math

    tags: dict = {}
    for col in columns:
        if col == "geometry":
            continue
        val = row[col]
        # Drop None and float NaN (covers pandas NaN without requiring pandas import)
        if val is None:
            continue
        try:
            if isinstance(val, float) and math.isnan(val):
                continue
        except (TypeError, ValueError):
            pass
        tags[col] = val
    return tags


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

OVERPASS_TIMEOUT = 60


def download_and_build_buildings(
    bbox: tuple[float, float, float, float] | None,
    center_lat: float,
    center_lon: float,
    transform: dict,
    map_width_mm: float,
    map_height_mm: float,
    margin_mm: float,
    debug: bool = False,
    z_offset: float = 0.0,
    embed_depth_mm: float = 0.0,
    max_print_height_mm: float = 31.75,
    min_building_height_mm: float = 0.4,
    building_default_height_m: float = 6.0,
    building_levels_to_m: float = 3.0,
    building_max_real_height_m: float = 400.0,
    building_clip_threshold: float = 0.5,
    radius_m: float | None = None,
    buildings_file: str | None = None,
    overlay_roads: object | None = None,
    route_points: np.ndarray | None = None,
) -> tuple[geom.base.BaseGeometry | None, object | None]:
    """Download building footprints within bbox and return (unioned_polygons, mesh).

    Each building's visible height is proportional to its real-world OSM height so
    that the tallest building in the scene prints at ``max_print_height_mm``.  Buildings
    where more than ``building_clip_threshold`` of their footprint lies outside the
    margin-inset build area are omitted; the remainder are clipped to that boundary.

    ``embed_depth_mm`` extends every building below the base top without reducing that
    visible height. All extruded buildings are concatenated into a single mesh.
    """
    # geo_with_tags: list of (shapely_geometry, tags_dict) collected from all sources
    geo_with_tags: list[tuple] = []

    if buildings_file is not None:
        try:
            import geopandas as gpd

            df = gpd.read_file(buildings_file)
            cols = list(df.columns)
            for _, row in df.iterrows():
                g = row.geometry
                if g is None:
                    continue
                geo_with_tags.append((g, _tags_from_gdf_row(row, cols)))
        except Exception as exc:
            logging.warning("Failed to read local buildings file %s: %s", buildings_file, exc)
            return None, None
    else:
        try:
            import osmnx as ox
        except Exception as exc:
            logging.warning("OSMnx not available or failed to import: %s", exc)
            return None, None

        bbox_for_query: tuple | None = None

        for endpoint in OVERPASS_ENDPOINTS:
            try:
                ox.settings.overpass_endpoint = endpoint

                if radius_m is not None:
                    try:
                        gdf = ox.geometries_from_point(
                            (center_lat, center_lon), tags={"building": True}, dist=radius_m
                        )
                        cols = list(gdf.columns)
                        for _, row in gdf.iterrows():
                            g = row.geometry
                            if g is None:
                                continue
                            geo_with_tags.append((g, _tags_from_gdf_row(row, cols)))
                        break
                    except Exception:
                        lat_delta = radius_m / 111000.0
                        lon_delta = radius_m / (111000.0 * max(0.000001, np.cos(np.deg2rad(center_lat))))
                        bbox_for_query = (
                            center_lat - lat_delta,
                            center_lon - lon_delta,
                            center_lat + lat_delta,
                            center_lon + lon_delta,
                        )
                else:
                    lat_min, lat_max, lon_min, lon_max = bbox
                    try:
                        gdf = ox.geometries_from_bbox(
                            lat_max, lat_min, lon_max, lon_min, tags={"building": True}
                        )
                        cols = list(gdf.columns)
                        for _, row in gdf.iterrows():
                            g = row.geometry
                            if g is None:
                                continue
                            geo_with_tags.append((g, _tags_from_gdf_row(row, cols)))
                        break
                    except Exception:
                        bbox_for_query = (lat_min, lon_min, lat_max, lon_max)

                # OSMnx failed — fall back to raw Overpass HTTP query
                if bbox_for_query is None:
                    geo_with_tags = []
                    continue

                try:
                    import requests
                    from shapely.geometry import Polygon as ShapelyPolygon

                    south, west, north, east = bbox_for_query
                    # Use "out body geom" so tags are included in the response
                    query = (
                        f"[out:json][timeout:25];\n"
                        f"(\n"
                        f'  way["building"]({south},{west},{north},{east});\n'
                        f");\n"
                        f"out body geom;\n"
                    )
                    headers = {
                        "User-Agent": "memorymap-pipeline/1.0 (+https://example.local)",
                        "Accept": "application/json",
                    }
                    resp = requests.post(
                        endpoint, data={"data": query}, headers=headers, timeout=OVERPASS_TIMEOUT
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    elements = data.get("elements", [])
                    for el in elements:
                        if el.get("type") != "way":
                            continue
                        geom_coords = el.get("geometry")
                        if not geom_coords:
                            continue
                        coords = [
                            (c["lon"], c["lat"])
                            for c in geom_coords
                            # Overpass geometry entries are always dicts; skip any malformed elements
                            if isinstance(c, dict) and "lon" in c and "lat" in c
                        ]
                        el_tags = el.get("tags", {})
                        try:
                            geo_with_tags.append((ShapelyPolygon(coords), el_tags))
                        except Exception:
                            continue
                    if geo_with_tags:
                        break
                except Exception as exc:
                    logging.warning("Overpass HTTP fetch failed via %s: %s", endpoint, exc)
                    geo_with_tags = []
                    continue

            except Exception as exc:
                logging.warning("Failed to download OSM building data via %s: %s", endpoint, exc)
                geo_with_tags = []
                continue

        if not geo_with_tags:
            return None, None

    # Margin-inset plate boundary used for clip/omit decisions
    plate_box = geom.box(
        margin_mm, margin_mm, map_width_mm - margin_mm, map_height_mm - margin_mm
    )

    # Transform each polygon to mm coords, extract its height, clip, and filter
    poly_height_pairs: list[tuple[geom.base.BaseGeometry, float]] = []

    for g, tags in geo_with_tags:
        if g is None:
            continue
        if g.geom_type == "Polygon":
            polys = [g]
        elif g.geom_type == "MultiPolygon":
            polys = list(g.geoms)
        else:
            continue

        real_h = _extract_real_height_m(
            tags,
            default_height_m=building_default_height_m,
            levels_to_m=building_levels_to_m,
            max_height_m=building_max_real_height_m,
        )

        for p in polys:
            if p.is_empty:
                continue

            try:
                tp = _transform_shapely_polygon(
                    p, center_lat=center_lat, center_lon=center_lon, transform=transform
                )
            except Exception:
                continue

            if not tp.is_valid:
                tp, _ok, _exp = repair_polygon(tp)
            if tp.is_empty:
                continue

            original_area = tp.area
            if original_area <= 0.0:
                continue

            # Clip to margin-inset plate boundary
            try:
                clipped = tp.intersection(plate_box)
            except Exception:
                continue

            if clipped.is_empty:
                continue

            fraction_inside = clipped.area / original_area
            # Omit buildings where the fraction of footprint inside the build area is
            # less than (1 - building_clip_threshold).  With a threshold of 0.5 this
            # discards any building that has more than 50 % of its area outside.
            if fraction_inside < (1.0 - building_clip_threshold):
                continue

            if not clipped.is_valid:
                clipped, _ok, _exp = repair_polygon(clipped)
            if clipped.is_empty:
                continue

            poly_height_pairs.append((clipped, real_h))

    if not poly_height_pairs:
        return None, None

    # Proportional height scaling: tallest building in scene → max_print_height_mm
    all_real_heights = [h for _, h in poly_height_pairs]
    max_real_h = max(all_real_heights)
    height_scale = (max_print_height_mm / max_real_h) if max_real_h > 0.0 else 1.0

    logging.info(
        "Buildings: %d footprints | real heights %.1f–%.1f m | "
        "scale %.4f mm/m | extrusions %.2f–%.2f mm",
        len(poly_height_pairs),
        min(all_real_heights),
        max_real_h,
        height_scale,
        min(max(min_building_height_mm, h * height_scale) for _, h in poly_height_pairs),
        max(max(min_building_height_mm, h * height_scale) for _, h in poly_height_pairs),
    )

    # Extrude each building individually then concatenate into one mesh
    meshes = []
    for poly, real_h in poly_height_pairs:
        extrusion_mm = max(min_building_height_mm, real_h * height_scale)
        parts: list[geom.Polygon] = (
            list(poly.geoms) if poly.geom_type == "MultiPolygon" else [poly]
        )
        for part in parts:
            if part.is_empty:
                continue
            try:
                meshes.append(
                    route_mesh_from_polygon(
                        part,
                        height_mm=extrusion_mm + embed_depth_mm,
                        z_offset=z_offset,
                    )
                )
            except Exception as exc:
                logging.warning("Failed creating building mesh: %s", exc)

    final_mesh = None
    if meshes:
        try:
            from trimesh.util import concatenate

            final_mesh = concatenate(meshes)
        except Exception:
            final_mesh = meshes[0]

    # Union of all clipped footprints (used for debug overlay and return value)
    unioned = ops.unary_union([p for p, _ in poly_height_pairs])

    if debug:
        try:
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(6, 8))
            for poly, _real_h in poly_height_pairs:
                try:
                    parts = (
                        list(poly.geoms) if poly.geom_type == "MultiPolygon" else [poly]
                    )
                    for part in parts:
                        x, y = part.exterior.xy
                        ax.fill(x, y, alpha=0.6, fc="cyan", ec="black")
                except Exception:
                    pass
            if overlay_roads is not None:
                try:
                    road_parts = (
                        list(overlay_roads.geoms)
                        if overlay_roads.geom_type in ("MultiPolygon", "GeometryCollection")
                        else [overlay_roads]
                    )
                    for r in road_parts:
                        if r.is_empty or not hasattr(r, "exterior"):
                            continue
                        x, y = r.exterior.xy
                        ax.plot(x, y, color="black", linewidth=0.5)
                except Exception:
                    pass
            if route_points is not None:
                try:
                    ax.plot(route_points[:, 0], route_points[:, 1], color="red", linewidth=1.0)
                except Exception:
                    pass
            ax.set_aspect("equal", adjustable="box")
            fig.savefig("buildings_debug.png", dpi=150)
            plt.close(fig)
        except Exception:
            pass

    return unioned, final_mesh

