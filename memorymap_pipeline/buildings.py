from __future__ import annotations

from typing import Iterable
import logging

import shapely.geometry as geom
import shapely.ops as ops
import numpy as np

from .projection import project_lonlat_array, apply_transform
from .geometry import repair_polygon
from .mesh import route_mesh_from_polygon


def _transform_shapely_polygon(polygon: geom.Polygon, center_lat: float, center_lon: float, transform: dict) -> geom.Polygon:
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
        transformed = apply_transform(projected, transform)
        return [(float(x), float(y)) for x, y in transformed]

    exterior = _transform_ring(polygon.exterior.coords)
    interiors = [_transform_ring(r.coords) for r in polygon.interiors]
    try:
        return geom.Polygon(exterior, interiors)
    except Exception:
        return polygon


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def download_and_build_buildings(
    bbox: tuple[float, float, float, float] | None,
    center_lat: float,
    center_lon: float,
    transform: dict,
    debug: bool = False,
    z_offset: float = 0.0,
    building_thickness_mm: float = 0.3,
    radius_m: float | None = None,
    buildings_file: str | None = None,
    overlay_roads: object | None = None,
    route_points: np.ndarray | None = None,
) -> tuple[geom.base.BaseGeometry | None, object | None]:
    """Download building footprints within bbox and return (unioned_polygons, mesh).

    Polygons are transformed into map mm coordinates using the provided `transform` and
    `project_lonlat_array`, and extruded to `building_thickness_mm` at `z_offset`.
    """
    Polygon = None
    MultiPolygon = None

    if buildings_file is not None:
        try:
            import geopandas as gpd
            from shapely.geometry import Polygon, MultiPolygon

            df = gpd.read_file(buildings_file)
            geoms = df.geometry
        except Exception as exc:
            logging.warning("Failed to read local buildings file %s: %s", buildings_file, exc)
            return None, None
    else:
        try:
            import osmnx as ox
            from shapely.geometry import Polygon, MultiPolygon
        except Exception as exc:
            logging.warning("OSMnx not available or failed to import: %s", exc)
            return None, None

        geoms = None
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                ox.settings.overpass_endpoint = endpoint
                # Prefer using osmnx helper if available
                if radius_m is not None:
                    tags = {"building": True}
                    try:
                        gdf = ox.geometries_from_point((center_lat, center_lon), tags=tags, dist=radius_m)
                        geoms = gdf.geometry
                        break
                    except Exception:
                        # fall back to Overpass HTTP using bbox computed from radius
                        lat_delta = radius_m / 111000.0
                        lon_delta = radius_m / (111000.0 * max(0.000001, np.cos(np.deg2rad(center_lat))))
                        north = center_lat + lat_delta
                        south = center_lat - lat_delta
                        east = center_lon + lon_delta
                        west = center_lon - lon_delta
                        bbox_for_query = (south, west, north, east)
                else:
                    lat_min, lat_max, lon_min, lon_max = bbox
                    # ox.geometries_from_bbox takes north, south, east, west
                    try:
                        gdf = ox.geometries_from_bbox(lat_max, lat_min, lon_max, lon_min, tags={"building": True})
                        geoms = gdf.geometry
                        break
                    except Exception:
                        bbox_for_query = (lat_min, lon_min, lat_max, lon_max)

                # If osmnx helpers failed, perform Overpass HTTP query directly
                try:
                    import requests

                    south, west, north, east = bbox_for_query
                    query = f"""
[out:json][timeout:25];
(
  way["building"]({south},{west},{north},{east});
  relation["building"]({south},{west},{north},{east});
);
out geom;
"""
                    headers = {"User-Agent": "memorymap-pipeline/1.0 (+https://example.local)", "Accept": "application/json"}
                    resp = requests.post(endpoint, data={"data": query}, headers=headers, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                    elements = data.get("elements", [])
                    # Build shapely geometries from returned elements
                    from shapely.geometry import Polygon as ShapelyPolygon

                    parsed = []
                    for el in elements:
                        geom_coords = el.get("geometry")
                        if not geom_coords:
                            continue
                        coords = [(c["lon"], c["lat"]) if isinstance(c, dict) else (c["lon"], c["lat"]) for c in geom_coords]
                        try:
                            parsed.append(ShapelyPolygon(coords))
                        except Exception:
                            continue
                    if parsed:
                        geoms = parsed
                        break
                except Exception as exc:
                    logging.warning("Overpass HTTP fetch failed via %s: %s", endpoint, exc)
                    geoms = None
                    continue
            except Exception as exc:
                logging.warning("Failed to download OSM building data via %s: %s", endpoint, exc)
                geoms = None
                continue

        if geoms is None:
            return None, None

    polygons = []
    for g in geoms:
        if g is None:
            continue
        geom_type = g.geom_type
        if geom_type == "Polygon":
            polys = [g]
        elif geom_type == "MultiPolygon":
            polys = list(g.geoms)
        else:
            continue

        for p in polys:
            if p.is_empty:
                continue
            try:
                tp = _transform_shapely_polygon(p, center_lat=center_lat, center_lon=center_lon, transform=transform)
            except Exception:
                tp = p

            if not tp.is_valid:
                tp, ok, explanation = repair_polygon(tp)
            if tp.is_empty:
                continue
            polygons.append(tp)

    if not polygons:
        return None, None

    unioned = ops.unary_union(polygons)

    # create meshes for each polygon part, preserving geometry
    meshes = []
    try:
        if unioned.geom_type == "Polygon":
            parts = [unioned]
        else:
            parts = list(unioned.geoms)
        for p in parts:
            if p.is_empty:
                continue
            mesh = route_mesh_from_polygon(p, height_mm=building_thickness_mm, z_offset=z_offset)
            meshes.append(mesh)
    except Exception as exc:
        logging.warning("Failed creating building meshes: %s", exc)

    final_mesh = None
    if meshes:
        try:
            from trimesh.util import concatenate

            final_mesh = concatenate(meshes)
        except Exception:
            final_mesh = meshes[0]

    if debug:
        try:
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(6, 8))
            # plot buildings
            for poly in polygons:
                try:
                    x, y = poly.exterior.xy
                    ax.fill(x, y, alpha=0.6, fc="cyan", ec="black")
                except Exception:
                    pass
            # overlay roads if given
            if overlay_roads is not None:
                try:
                    if hasattr(overlay_roads, 'exterior'):
                        parts = [overlay_roads]
                    else:
                        parts = list(overlay_roads.geoms) if overlay_roads.geom_type == 'MultiPolygon' else [overlay_roads]
                    for r in parts:
                        if r.is_empty:
                            continue
                        x, y = r.exterior.xy
                        ax.plot(x, y, color='black', linewidth=0.5)
                except Exception:
                    pass
            # overlay route points if provided (scaled mm coords)
            if route_points is not None:
                try:
                    ax.plot(route_points[:, 0], route_points[:, 1], color='red', linewidth=1.0)
                except Exception:
                    pass

            ax.set_aspect("equal", adjustable="box")
            fig.savefig("buildings_debug.png", dpi=150)
            plt.close(fig)
        except Exception:
            pass

    return unioned, final_mesh
