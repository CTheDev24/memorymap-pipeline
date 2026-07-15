from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from shapely.geometry import box

from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.landscape import LANDSCAPE_TAGS, download_landscape_polygons
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.terrain import ElevationGrid


FIXTURES = Path(__file__).parent / "fixtures"


def _materials(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        model = next(name for name in archive.namelist() if name.lower().endswith(".model"))
        root = ET.fromstring(archive.read(model))
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    palettes = {
        item.attrib["id"]: [base.attrib["displaycolor"] for base in item]
        for item in root.findall("m:resources/m:basematerials", ns)
    }
    return {
        item.attrib["name"]: palettes[item.attrib["pid"]][int(item.attrib["pindex"])]
        for item in root.findall("m:resources/m:object", ns)
        if "pid" in item.attrib
    }


def _assembly_parts(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        model = next(name for name in archive.namelist() if name.lower().endswith(".model"))
        root = ET.fromstring(archive.read(model))
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    objects = {
        item.attrib["id"]: item
        for item in root.findall("m:resources/m:object", ns)
    }
    build_item = root.find("m:build/m:item", ns)
    assert build_item is not None
    assembly = objects[build_item.attrib["objectid"]]
    return [
        objects[item.attrib["objectid"]].attrib["name"]
        for item in assembly.findall("m:components/m:component", ns)
    ]


def test_reliable_osm_polygon_tags_exclude_ambiguous_land_cover() -> None:
    assert "farmland" not in LANDSCAPE_TAGS["landuse"]
    assert "bare_rock" not in LANDSCAPE_TAGS["natural"]
    assert set(LANDSCAPE_TAGS["natural"]) >= {"wood", "grassland", "beach", "sand"}


def test_offline_fixture_filters_and_transforms_vegetation_and_sand() -> None:
    frame = MapFrame(29.76, -95.37, 180, 140, 120, 90, 0, 5)
    polygons = download_landscape_polygons(
        bbox=(29.759, 29.761, -95.371, -95.369),
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        map_width_mm=frame.print_width_mm,
        map_height_mm=frame.print_height_mm,
        landscape_file=FIXTURES / "landscape_polygons.geojson",
    )
    assert len(polygons["vegetation"]) == 1
    assert len(polygons["sand"]) == 1


def test_landscape_mode_exports_named_precolored_draped_parts(tmp_path: Path) -> None:
    route = load_route_from_gpx(FIXTURES / "frame_route.gpx")
    frame = MapFrame(29.76, -95.37, 180, 140, 120, 90, 0, 5)
    grid = ElevationGrid(
        np.array([[10.0, 12.0, 14.0], [9.0, 11.0, 13.0], [8.0, 10.0, 12.0]]),
        29.759,
        29.761,
        -95.371,
        -95.369,
        "offline-fixture",
    )
    output = tmp_path / "landscape.3mf"
    result = generate_memory_map(GenerationRequest(
        route=route,
        frame=frame,
        output_path=output,
        include_route=False,
        include_roads=False,
        include_buildings=False,
        elevation_grid=grid,
        water_polygons=[box(50, 35, 70, 50)],
        landscape_file=FIXTURES / "landscape_polygons.geojson",
        config={
            "terrain_enabled": True,
            "water_enabled": True,
            "landscape_materials_enabled": True,
            "terrain_grid_size": 3,
        },
    ))

    assert _materials(output) == {
        "Terrain_Green": "#4F8A3CFF",
        "Water_Blue": "#3B82D0FF",
        "Vegetation_Green": "#4F8A3CFF",
        "Sand_Tan": "#D8B878FF",
    }
    assert set(_assembly_parts(output)) == {
        "Terrain_Green",
        "Water_Blue",
        "Vegetation_Green",
        "Sand_Tan",
    }
    assert result.vegetation_mesh is not None
    assert result.sand_mesh is not None
    assert result.water_mesh is not None
    for mesh in (result.vegetation_mesh, result.sand_mesh, result.water_mesh):
        assert mesh.bounds[0, 0] >= frame.margin_mm
        assert mesh.bounds[0, 1] >= frame.margin_mm
        assert mesh.bounds[1, 0] <= frame.print_width_mm - frame.margin_mm
        assert mesh.bounds[1, 1] <= frame.print_height_mm - frame.margin_mm
    # The material skins follow non-flat elevation rather than becoming flat slabs.
    assert np.ptp(result.vegetation_mesh.vertices[:, 2]) > 0.2
