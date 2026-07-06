from __future__ import annotations

from typing import Iterable
import logging

import shapely.geometry as geom
import shapely.ops as ops
import numpy as np

from .projection import project_lonlat_array, compute_normalize_center_transform, apply_transform
from .geometry import validate_polygon, repair_polygon
from .mesh import route_mesh_from_polygon


def _normalize_highway_value(highway):
    if isinstance(highway, (list, tuple)):
        return highway[0]
    return highway


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def download_and_build_roads(
    bbox: tuple[float, float, float, float] | None,
    center_lat: float,
    center_lon: float,
    transform: dict,
    road_types: Iterable[str],
    road_widths: dict,
    road_height_mm: float,
    debug: bool = False,
    z_offset: float = 0.0,
    radius_m: float | None = None,
    roads_file: str | None = None,
) -> tuple[geom.base.BaseGeometry | None, object | None]:
    """Download OSM drivable roads within bbox (lat_min, lat_max, lon_min, lon_max), buffer them
    using widths from road_widths (mm), and extrude into a mesh sitting on top of the base.

    Returns (buffered_polygons (shapely), mesh_or_none).
    """
    # If a local roads file is provided, use geopandas to load it instead of OSMnx
    LineString = None
    MultiPolygon = None
    Polygon = None
    if roads_file is not None:
        try:
            import geopandas as gpd
            from shapely.geometry import LineString, MultiPolygon, Polygon

            edges = gpd.read_file(roads_file)
            # Expect the GeoDataFrame to contain LineString geometries
        except Exception as exc:
            logging.warning("Failed to read local roads file %s: %s", roads_file, exc)
            return None, None
    else:
        try:
            import osmnx as ox
            from shapely.geometry import LineString, MultiPolygon, Polygon
        except Exception as exc:  # pragma: no cover - optional dependency
            logging.warning("OSMnx not available or failed to import: %s", exc)
            return None, None

        edges = None
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                ox.settings.overpass_endpoint = endpoint
                if radius_m is not None:
                    G = ox.graph_from_point((center_lat, center_lon), dist=radius_m, network_type="drive")
                else:
                    lat_min, lat_max, lon_min, lon_max = bbox
                    bbox_tuple = (lat_max, lat_min, lon_max, lon_min)
                    G = ox.graph_from_bbox(bbox_tuple, network_type="drive")
                edges = ox.graph_to_gdfs(G, nodes=False, edges=True, fill_edge_geometry=True)
                break
            except Exception as exc:
                logging.warning("Failed to download OSM data via %s: %s", endpoint, exc)
                edges = None

        if edges is None:
            return None, None

    buffered_polys = []

    for _, row in edges.iterrows():
        hw = row.get("highway")
        if hw is None:
            continue
        hw_norm = _normalize_highway_value(hw)
        if hw_norm not in road_types:
            continue

        geom_obj = row.get("geometry")
        if geom_obj is None:
            # try to build from u/v
            u = row.get("u")
            v = row.get("v")
            continue

        # extract lat/lon arrays (shapely gives coords as (lon, lat))
        coords = np.array(geom_obj.coords)
        if coords.shape[0] < 2:
            continue
        lons = coords[:, 0]
        lats = coords[:, 1]

        projected = project_lonlat_array(lats, lons, center_lat=center_lat, center_lon=center_lon)
        transformed = apply_transform(projected, transform)

        # build shapely LineString in mm coordinates
        line = LineString([(float(x), float(y)) for x, y in transformed])

        width_mm = float(road_widths.get(hw_norm, 1.0))
        poly = line.buffer(width_mm / 2.0, resolution=16, cap_style=2, join_style=1)
        if not poly.is_empty:
            buffered_polys.append(poly)

    if not buffered_polys:
        return None, None

    unioned = ops.unary_union(buffered_polys)
    if not unioned.is_valid:
        unioned, ok, explanation = repair_polygon(unioned)

    # extrude polygons to mesh
    meshes = []
    try:
        if isinstance(unioned, (MultiPolygon, Polygon)):
            parts = [unioned] if isinstance(unioned, Polygon) else list(unioned.geoms)
            for p in parts:
                if p.is_empty:
                    continue
                mesh = route_mesh_from_polygon(p, height_mm=road_height_mm, z_offset=z_offset)
                meshes.append(mesh)
    except Exception as exc:  # pragma: no cover - mesh library issues
        logging.warning("Failed creating road meshes: %s", exc)

    # try to combine meshes if available
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
            # show roads centerlines and buffered polygons
            for poly in buffered_polys:
                if poly.is_empty:
                    continue
                x, y = poly.exterior.xy
                ax.fill(x, y, alpha=0.6, fc="black", ec="none")
            ax.set_aspect("equal", adjustable="box")
            fig.savefig("roads_buffered_debug.png", dpi=150)
            plt.close(fig)
        except Exception:
            pass

    return unioned, final_mesh
