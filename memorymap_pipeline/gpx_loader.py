from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List
import xml.etree.ElementTree as ET


@dataclass
class RoutePoint:
    latitude: float
    longitude: float
    elevation: float | None = None


@dataclass
class Route:
    points: List[RoutePoint]


def load_route_from_gpx(path: str | Path) -> Route:
    tree = ET.parse(path)
    root = tree.getroot()

    points: list[RoutePoint] = []
    namespace = {"gpx": "http://www.topografix.com/GPX/1/1"}
    trkpts = root.findall(".//gpx:trkpt", namespace) or root.findall(".//{*}trkpt")

    for trkpt in trkpts:
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        ele = None
        ele_node = trkpt.find("gpx:ele", namespace)
        if ele_node is None:
            ele_node = trkpt.find("{*}ele")
        if ele_node is not None and ele_node.text is not None:
            ele = float(ele_node.text)
        points.append(RoutePoint(latitude=lat, longitude=lon, elevation=ele))

    if not points:
        raise ValueError("No track points found in GPX file")

    return Route(points=points)
