from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import logging

import shapely.geometry as geom
import shapely.ops as ops
import numpy as np

from .projection import apply_transform, project_lonlat_array
from .geometry import repair_polygon
from .mesh import route_mesh_from_polygon


@dataclass(frozen=True)
class RoadTerrainCorridor:
    centerline: geom.LineString
    region: geom.base.BaseGeometry
    width_mm: float
    classification: str


def _normalize_highway_value(highway):
    if isinstance(highway, (list, tuple)):
        return highway[0]
    return highway


def _normalized_tag_values(value: object) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {
            str(item).strip().lower()
            for item in value
            if str(item).strip()
        }
    if value is None:
        return set()
    normalized = str(value).strip().lower()
    return {normalized} if normalized else set()


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

OVERPASS_TIMEOUT = 60

_ROAD_EXTRUSION_MAX_SPLIT_DEPTH = 8
_ROAD_EXTRUSION_MIN_AREA_MM2 = 0.01
_ROAD_EXTRUSION_SIMPLIFY_MM = 0.002


def _polygon_parts(shape) -> list[geom.Polygon]:
    if isinstance(shape, geom.Polygon):
        return [] if shape.is_empty else [shape]
    if isinstance(shape, geom.MultiPolygon):
        return [part for part in shape.geoms if not part.is_empty]
    if hasattr(shape, "geoms"):
        return [
            part
            for child in shape.geoms
            for part in _polygon_parts(child)
        ]
    return []


def _bisect_polygon(polygon: geom.Polygon) -> list[geom.Polygon]:
    """Split a complex polygon without changing its printable footprint."""
    min_x, min_y, max_x, max_y = polygon.bounds
    width = max_x - min_x
    height = max_y - min_y
    padding = max(width, height, 1.0) + 1.0
    if width >= height:
        midpoint = (min_x + max_x) / 2.0
        cutter = geom.LineString(
            [(midpoint, min_y - padding), (midpoint, max_y + padding)]
        )
    else:
        midpoint = (min_y + max_y) / 2.0
        cutter = geom.LineString(
            [(min_x - padding, midpoint), (max_x + padding, midpoint)]
        )

    try:
        pieces = _polygon_parts(ops.split(polygon, cutter))
    except Exception:
        return []
    return [
        piece
        for piece in pieces
        if piece.area >= _ROAD_EXTRUSION_MIN_AREA_MM2
    ]


def _extrude_road_polygon(
    polygon: geom.Polygon,
    *,
    height_mm: float,
    z_offset: float,
    split_depth: int = 0,
) -> list[object]:
    """Extrude a road polygon, subdividing only when triangulation is invalid.

    Dense urban road buffers often union into one polygon with thousands of
    vertices and holes.  A triangulation failure in that single polygon used to
    discard the entire connected street network.  Spatial bisection keeps the
    same footprint while giving the triangulator smaller, independently
    watertight solids that slicers can combine as one road object.
    """
    try:
        return [
            route_mesh_from_polygon(
                polygon,
                height_mm=height_mm,
                z_offset=z_offset,
            )
        ]
    except Exception as exc:
        simplified = polygon.simplify(
            _ROAD_EXTRUSION_SIMPLIFY_MM,
            preserve_topology=True,
        )
        if (
            not simplified.is_empty
            and isinstance(simplified, geom.Polygon)
            and len(simplified.exterior.coords) < len(polygon.exterior.coords)
        ):
            try:
                return [
                    route_mesh_from_polygon(
                        simplified,
                        height_mm=height_mm,
                        z_offset=z_offset,
                    )
                ]
            except Exception:
                pass
        if (
            split_depth >= _ROAD_EXTRUSION_MAX_SPLIT_DEPTH
            or polygon.area < 2.0 * _ROAD_EXTRUSION_MIN_AREA_MM2
        ):
            logging.warning(
                "Skipping invalid road polygon after %d subdivision levels: %s",
                split_depth,
                exc,
            )
            return []

        pieces = _bisect_polygon(polygon)
        if len(pieces) < 2:
            logging.warning(
                "Skipping invalid road polygon that could not be subdivided: %s",
                exc,
            )
            return []

        meshes = []
        for piece in pieces:
            meshes.extend(
                _extrude_road_polygon(
                    piece,
                    height_mm=height_mm,
                    z_offset=z_offset,
                    split_depth=split_depth + 1,
                )
            )
        return meshes


def download_and_build_roads(
    bbox: tuple[float, float, float, float] | None,
    center_lat: float,
    center_lon: float,
    transform: dict,
    road_types: Iterable[str],
    road_widths: dict,
    road_height_mm: float,
    network_type: str = "all",
    map_width_mm: float = 190.0,
    map_height_mm: float = 190.0,
    margin_mm: float = 8.0,
    debug: bool = False,
    z_offset: float = 0.0,
    embed_depth_mm: float = 0.0,
    radius_m: float | None = None,
    roads_file: str | None = None,
    terrain_smoothing_types: Iterable[str] = (),
    excluded_service_types: Iterable[str] = (),
    excluded_access: Iterable[str] = (),
    priority_region: geom.base.BaseGeometry | None = None,
) -> tuple[geom.base.BaseGeometry | None, object | None]:
    """Download OSM drivable roads within bbox (lat_min, lat_max, lon_min, lon_max), buffer them
    using widths from road_widths (mm). ``road_height_mm`` is the visible height above
    the base; ``embed_depth_mm`` extends the mesh downward for a reliable overlap.

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
                    G = ox.graph_from_point((center_lat, center_lon), dist=radius_m, network_type=network_type)
                else:
                    lat_min, lat_max, lon_min, lon_max = bbox
                    bbox_tuple = (lat_max, lat_min, lon_max, lon_min)
                    G = ox.graph_from_bbox(bbox_tuple, network_type=network_type)
                edges = ox.graph_to_gdfs(G, nodes=False, edges=True, fill_edge_geometry=True)
                break
            except Exception as exc:
                logging.warning("Failed to download OSM data via %s: %s", endpoint, exc)
                edges = None

        if edges is None:
            return None, None

    buffered_polys = []
    smoothing_polys = []
    smoothing_corridors = []
    smoothing_corridor_keys = set()
    smoothing_types = set(terrain_smoothing_types)
    blocked_service_types = {
        str(value).strip().lower() for value in excluded_service_types
    }
    blocked_access = {
        str(value).strip().lower() for value in excluded_access
    }

    # Create clipping boundary to keep roads within map bounds
    clip_box = geom.box(
        margin_mm, margin_mm, map_width_mm - margin_mm, map_height_mm - margin_mm
    )

    for _, row in edges.iterrows():
        hw = row.get("highway")
        if hw is None:
            continue
        hw_norm = _normalize_highway_value(hw)
        if hw_norm not in road_types:
            continue
        if (
            hw_norm == "service"
            and _normalized_tag_values(row.get("service"))
            & blocked_service_types
        ):
            continue
        if _normalized_tag_values(row.get("access")) & blocked_access:
            continue

        geom_obj = row.get("geometry")
        if geom_obj is None:
            continue

        # extract lat/lon arrays (shapely gives coords as (lon, lat))
        coords = np.array(geom_obj.coords)
        if coords.shape[0] < 2:
            continue
        lons = coords[:, 0]
        lats = coords[:, 1]

        projected = project_lonlat_array(lats, lons, center_lat=center_lat, center_lon=center_lon)
        frame = transform.get("map_frame")
        transformed = frame.transform_projected(projected) if frame is not None else apply_transform(projected, transform)

        # build shapely LineString in mm coordinates
        line = LineString([(float(x), float(y)) for x, y in transformed])

        width_mm = float(road_widths.get(hw_norm, 1.0))
        poly = line.buffer(width_mm / 2.0, resolution=16, cap_style=2, join_style=1)
        if not poly.is_empty:
            # Clip road to map boundaries
            try:
                clipped = poly.intersection(clip_box)
                if not clipped.is_empty:
                    buffered_polys.append(clipped)
                    if hw_norm in smoothing_types:
                        smoothing_polys.append(clipped)
                        key = (hw_norm, line.normalize().wkb)
                        if key not in smoothing_corridor_keys:
                            smoothing_corridor_keys.add(key)
                            smoothing_corridors.append(
                                RoadTerrainCorridor(line, clipped, width_mm, hw_norm)
                            )
            except Exception:
                # If clipping fails, keep unclipped
                buffered_polys.append(poly)
                if hw_norm in smoothing_types:
                    smoothing_polys.append(poly)
                    key = (hw_norm, line.normalize().wkb)
                    if key not in smoothing_corridor_keys:
                        smoothing_corridor_keys.add(key)
                        smoothing_corridors.append(
                            RoadTerrainCorridor(line, poly, width_mm, hw_norm)
                        )

    if not buffered_polys:
        return None, None

    unioned = ops.unary_union(buffered_polys)
    # A geometric union may legally retain polygons which touch at only a point.
    # Slicers weld those coincident vertices and create edges shared by three or
    # more faces. A 0.01 mm morphological close is far below nozzle resolution,
    # but turns those contacts into a printable manifold region.
    topology_weld_mm = 0.01
    unioned = unioned.buffer(topology_weld_mm).buffer(-topology_weld_mm)
    unioned = unioned.intersection(clip_box)
    if priority_region is not None and not priority_region.is_empty:
        # Keep road material completely outside the highlighted route corridor. A
        # tiny clearance avoids coplanar preview fragments along the shared boundary.
        unioned = unioned.difference(priority_region.buffer(0.03)).buffer(0)
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
                meshes.extend(
                    _extrude_road_polygon(
                        p,
                        height_mm=road_height_mm + embed_depth_mm,
                        z_offset=z_offset,
                    )
                )
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
        if smoothing_polys:
            final_mesh.metadata["terrain_smoothing_region"] = ops.unary_union(
                smoothing_polys
            ).intersection(clip_box)
            final_mesh.metadata["terrain_profile_corridors"] = tuple(
                smoothing_corridors
            )

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
