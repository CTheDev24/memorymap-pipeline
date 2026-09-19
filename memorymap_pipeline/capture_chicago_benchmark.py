"""Acquire a provisional 2025 Chicago fixture; generation remains offline afterward.

The route is a community GPX, not an organizer-certified course. OSM extraction is
limited to three test windows plus a halo, not a complete city inventory.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import requests
from shapely import make_valid
from shapely.geometry import LineString, Polygon, mapping
from shapely.ops import polygonize, unary_union

from .footprint_benchmark import freeze
from .gpx_loader import load_route_from_gpx
from .map_frame import MapFrame

ROUTE_URL = "https://www.goandrace.com/gpx/2025/10/12/gpx_20251012_id10457_race1_20250826213208.gpx"
ROUTE_PAGE = (
    "https://www.goandrace.com/en/map/2025/bank-of-america-chicago-marathon-2025-course-map-1.php"
)
CROPS = [
    {"name": "downtown", "center_latlon": [41.883, -87.630], "size_mm": 25},
    {"name": "dense-neighborhood", "center_latlon": [41.940, -87.647], "size_mm": 25},
    {"name": "sparse-neighborhood", "center_latlon": [41.846, -87.640], "size_mm": 25},
]


def _polygon(element):
    if element["type"] == "way":
        points = [(p["lon"], p["lat"]) for p in element.get("geometry", [])]
        return make_valid(Polygon(points)) if len(points) >= 4 and points[0] == points[-1] else None
    rings = {"outer": [], "inner": []}
    for member in element.get("members", []):
        role = member.get("role") or "outer"
        points = [(p["lon"], p["lat"]) for p in member.get("geometry", [])]
        if role in rings and len(points) >= 2:
            rings[role].append(LineString(points))
    outer = unary_union(list(polygonize(unary_union(rings["outer"]))))
    inner = unary_union(list(polygonize(unary_union(rings["inner"]))))
    return make_valid(outer.difference(inner))


def capture(output: Path, resume: bool = False) -> Path:
    output.mkdir(parents=True, exist_ok=resume)
    session = requests.Session()
    session.headers["User-Agent"] = "MemoryMap-footprint-benchmark/1.0"
    route_path = output / "route.gpx"
    if not resume or not route_path.exists():
        response = session.get(ROUTE_URL, timeout=45)
        response.raise_for_status()
        route_path.write_bytes(response.content)
    route = load_route_from_gpx(route_path)
    frame = MapFrame.fit_route(route.points, 190, 240, 5, route_padding_mm=0.6)
    collections: dict[str, dict] = {key: {} for key in ("buildings", "roads", "water")}
    query_bounds = []
    for crop in CROPS:
        lat, lon = crop["center_latlon"]
        x, y = frame.transform_lonlat(np.array([lat]), np.array([lon]))[0]
        # Two extra print millimeters retain features crossing specimen boundaries.
        half = crop["size_mm"] / 2 + 2
        latitudes, longitudes = frame.print_to_lonlat(
            np.array([x - half, x + half]), np.array([y - half, y + half])
        )
        bounds = [
            float(latitudes.min()),
            float(longitudes.min()),
            float(latitudes.max()),
            float(longitudes.max()),
        ]
        query_bounds.append(bounds)
        bbox = ",".join(str(value) for value in bounds)
        selectors = (
            '["building"]',
            '["building:part"]',
            '["highway"]',
            '["natural"="water"]',
            '["waterway"]',
            '["landuse"="reservoir"]',
        )
        query = (
            "[out:json][timeout:90];("
            + "".join(
                f"{kind}{selector}({bbox});"
                for selector in selectors
                for kind in ("way", "relation")
            )
            + ");out body geom;"
        )
        raw_path = output / (crop["name"] + "-overpass.json")
        if resume and raw_path.exists():
            data = json.loads(raw_path.read_text(encoding="utf-8"))
        else:
            failures = []
            for endpoint in (
                "https://overpass-api.de/api/interpreter",
                "https://overpass.kumi.systems/api/interpreter",
                "https://overpass.private.coffee/api/interpreter",
            ):
                try:
                    response = session.post(endpoint, data={"data": query}, timeout=110)
                    response.raise_for_status()
                    data = response.json()
                    if data.get("remark"):
                        raise ValueError(data["remark"])
                    break
                except (requests.RequestException, ValueError) as exc:
                    failures.append(f"{endpoint}: {exc}")
            else:
                raise RuntimeError("Overpass capture failed: " + "; ".join(failures))
            (output / (crop["name"] + "-query.json")).write_text(
                json.dumps(
                    {
                        "endpoint": endpoint,
                        "query": query,
                        "acquired_at": datetime.now(UTC).isoformat(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        if data.get("remark"):
            raise ValueError(f"Incomplete Overpass response: {data['remark']}")
        raw_path.write_text(json.dumps(data), encoding="utf-8")
        for element in data.get("elements", []):
            tags = element.get("tags", {})
            identity = f"{element['type']}/{element['id']}"
            tags = {**tags, "_osm_id": element["id"], "_osm_type": element["type"]}
            if "building" in tags or "building:part" in tags:
                role, geometry = "buildings", _polygon(element)
            elif "highway" in tags or "waterway" in tags and element["type"] == "way":
                points = [(p["lon"], p["lat"]) for p in element.get("geometry", [])]
                if len(points) < 2:
                    continue
                role = "roads" if "highway" in tags else "water"
                geometry = LineString(points)
            else:
                role, geometry = "water", _polygon(element)
            if geometry is not None and not geometry.is_empty:
                collections[role][identity] = {
                    "type": "Feature",
                    "id": identity,
                    "properties": tags,
                    "geometry": mapping(geometry),
                }
        print(crop["name"], {key: len(value) for key, value in collections.items()}, flush=True)
    now = datetime.now(UTC).isoformat()
    sources = {
        "route": {
            "path": "route.gpx",
            "origin": ROUTE_PAGE,
            "acquired_at": now,
            "license": "Third-party course GPX; local evaluation only, redistribution not established",
        }
    }
    for role, features in collections.items():
        path = output / f"{role}.geojson"
        path.write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [features[key] for key in sorted(features)],
                }
            ),
            encoding="utf-8",
        )
        sources[role] = {
            "path": path.name,
            "origin": "https://overpass-api.de/api/interpreter",
            "acquired_at": now,
            "license": "ODbL 1.0; OpenStreetMap contributors",
        }
    spec = {
        "name": "Chicago 2025 provisional three-window benchmark v1",
        "sources": sources,
        "crops": CROPS,
        "coverage_note": "Community course approximation, not organizer-certified. Full-route XY scale; "
        "OSM buildings/roads/water only in three windows with 2 mm halo. "
        "Height distribution uses this combined subset, not all Chicago buildings. "
        "Dense/sparse neighborhood labels are intended test roles requiring visual confirmation.",
        "query_bounds_south_west_north_east": query_bounds,
    }
    spec_path = output / "spec.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return freeze(spec_path, output / "snapshot")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--resume", action="store_true", help="Reuse already downloaded raw responses"
    )
    args = parser.parse_args()
    print(capture(args.output, args.resume))


if __name__ == "__main__":
    main()
