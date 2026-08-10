"""Offline full-stack coverage for the frame-aware generation service.

The local fixtures deliberately cross the frame edges so this suite also catches
regressions where secondary layers use route-derived bounds instead of MapFrame.
"""
from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import pytest
from shapely.geometry import box

generation = pytest.importorskip(
    "memorymap_pipeline.generation",
    reason="frame-aware generation service is implemented in the GUI integration phase",
)

from memorymap_pipeline.config import load_config
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.mesh import build_base_plate, export_3mf, route_mesh_from_polygon


FIXTURES = Path(__file__).parent / "fixtures"


def test_optional_border_uses_full_plate_extents_when_disabled() -> None:
    frame = MapFrame(
        center_lat=29.76,
        center_lon=-95.37,
        coverage_width_m=1_000.0,
        coverage_height_m=800.0,
        print_width_mm=120.0,
        print_height_mm=90.0,
        margin_mm=5.0,
    )

    bordered = generation._frame_for_border(frame, True)
    borderless = generation._frame_for_border(frame, False)

    assert bordered is frame
    assert bordered.margin_mm == pytest.approx(5.0)
    assert borderless.margin_mm == pytest.approx(0.0)
    assert borderless.coverage_width_m == pytest.approx(frame.coverage_width_m)
    assert borderless.coverage_height_m == pytest.approx(frame.coverage_height_m)
    assert borderless.printable_width_mm == pytest.approx(frame.print_width_mm)
    assert borderless.printable_height_mm == pytest.approx(frame.print_height_mm)


def _model_objects(path: Path) -> tuple[set[str], list[tuple[float, float, float]]]:
    with zipfile.ZipFile(path) as archive:
        model_name = next(name for name in archive.namelist() if name.lower().endswith(".model"))
        root = ET.fromstring(archive.read(model_name))
    namespace = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    names = {item.attrib.get("name", "") for item in root.findall(".//m:object", namespace)}
    vertices = [
        (float(item.attrib["x"]), float(item.attrib["y"]), float(item.attrib["z"]))
        for item in root.findall(".//m:vertex", namespace)
    ]
    return names, vertices


def _model_materials(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        model_name = next(name for name in archive.namelist() if name.lower().endswith(".model"))
        root = ET.fromstring(archive.read(model_name))
    namespace = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    resources = root.find("m:resources", namespace)
    assert resources is not None
    palettes = {
        palette.attrib["id"]: [base.attrib["displaycolor"] for base in palette]
        for palette in resources.findall("m:basematerials", namespace)
    }
    return {
        item.attrib["name"]: palettes[item.attrib["pid"]][int(item.attrib["pindex"])]
        for item in resources.findall("m:object", namespace)
        if "pid" in item.attrib and "pindex" in item.attrib
    }


def _model_assembly(path: Path) -> tuple[str, list[str]]:
    with zipfile.ZipFile(path) as archive:
        model_name = next(name for name in archive.namelist() if name.lower().endswith(".model"))
        root = ET.fromstring(archive.read(model_name))
    namespace = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    objects = {
        item.attrib["id"]: item for item in root.findall("m:resources/m:object", namespace)
    }
    build_items = root.findall("m:build/m:item", namespace)
    assert len(build_items) == 1
    assembly = objects[build_items[0].attrib["objectid"]]
    components = assembly.findall("m:components/m:component", namespace)
    component_names = [
        objects[item.attrib["objectid"]].attrib["name"] for item in components
    ]
    return assembly.attrib["name"], component_names


def test_partial_export_assigns_materials_only_to_present_objects(tmp_path: Path) -> None:
    output = tmp_path / "route-only-layers.3mf"
    base = build_base_plate(40.0, 30.0, 1.0)
    route = route_mesh_from_polygon(box(5.0, 5.0, 35.0, 6.0), 2.2, -0.2)

    export_3mf(output, base, route)

    assert _model_materials(output) == {
        "Base_White": "#FFFFFFFF",
        "Route_Accent": "#FF6633FF",
    }
    assert _model_assembly(output) == ("MemoryMap", ["Base_White", "Route_Accent"])


def test_landscape_generation_uses_profile_route_height_by_default(tmp_path: Path) -> None:
    route = load_route_from_gpx(FIXTURES / "frame_route.gpx")
    frame = MapFrame(
        center_lat=29.7600,
        center_lon=-95.3700,
        coverage_width_m=180.0,
        coverage_height_m=140.0,
        print_width_mm=120.0,
        print_height_mm=90.0,
        margin_mm=5.0,
    )
    config = load_config()
    config["style_profile"] = "landscape"
    result = generation.generate_memory_map(
        generation.GenerationRequest(
            route=route,
            frame=frame,
            output_path=tmp_path / "landscape-route-height.3mf",
            include_roads=False,
            include_buildings=False,
            route_height_mm=None,
            base_thickness_mm=1.0,
            config=config,
        )
    )

    assert result.route_mesh is not None
    assert result.route_mesh.bounds[1, 2] == pytest.approx(1.2)
    assert result.stats["route_height_mm"] == pytest.approx(1.2)
    assert result.stats["route_height_source"] == "profile_default"


def test_generation_exports_supported_route_markers(tmp_path: Path) -> None:
    route = load_route_from_gpx(FIXTURES / "frame_route.gpx")
    frame = MapFrame(
        center_lat=29.7600,
        center_lon=-95.3700,
        coverage_width_m=180.0,
        coverage_height_m=140.0,
        print_width_mm=120.0,
        print_height_mm=90.0,
        margin_mm=5.0,
    )
    config = load_config()
    config["route_markers"] = "both"
    output = tmp_path / "route-markers.3mf"

    result = generation.generate_memory_map(
        generation.GenerationRequest(
            route=route,
            frame=frame,
            output_path=output,
            include_roads=False,
            include_buildings=False,
            base_thickness_mm=1.0,
            config=config,
        )
    )

    assert result.start_marker_mesh is not None
    assert result.finish_marker_mesh is not None
    assert result.stats["layers"]["start_marker"]["faces"] > 0
    assert result.stats["layers"]["finish_marker"]["faces"] > 0
    materials = _model_materials(output)
    assert materials["Start_Marker"] == materials["Route_Accent"]
    assert materials["Finish_Marker"] == materials["Route_Accent"]
    assert _model_assembly(output)[1][-2:] == ["Start_Marker", "Finish_Marker"]


def test_landscape_export_uses_bone_green_and_blue_material_bodies(
    tmp_path: Path,
) -> None:
    output = tmp_path / "landscape-layers.3mf"
    base = build_base_plate(40.0, 30.0, 1.6)
    green = route_mesh_from_polygon(box(5.0, 5.0, 25.0, 25.0), 0.6, -0.2)
    water = route_mesh_from_polygon(box(25.0, 5.0, 35.0, 25.0), 0.6, -0.2)

    export_3mf(
        output,
        base,
        None,
        water_mesh=water,
        landscape_mesh=green,
        style_profile="landscape",
    )

    assert _model_materials(output) == {
        "Base_Bone": "#D6CBABFF",
        "Water_Blue": "#3399FFFF",
        "Terrain_Green": "#4F772DFF",
    }
    assert _model_assembly(output) == (
        "MemoryMap",
        ["Base_Bone", "Water_Blue", "Terrain_Green"],
    )


def test_export_uses_custom_layer_colors(tmp_path: Path) -> None:
    output = tmp_path / "custom-colors.3mf"
    base = build_base_plate(40.0, 30.0, 1.0)
    route = route_mesh_from_polygon(box(5.0, 5.0, 35.0, 6.0), 2.2, -0.2)

    export_3mf(
        output,
        base,
        route,
        layer_colors={"base": "#123456", "route": "#ABCDEF"},
    )

    assert _model_materials(output) == {
        "Base_White": "#123456FF",
        "Route_Accent": "#ABCDEFFF",
    }


def test_full_generation_uses_frame_for_every_local_layer(tmp_path):
    route = load_route_from_gpx(FIXTURES / "frame_route.gpx")
    frame = MapFrame(
        center_lat=29.7600,
        center_lon=-95.3700,
        coverage_width_m=180.0,
        coverage_height_m=140.0,
        print_width_mm=120.0,
        print_height_mm=90.0,
        margin_mm=5.0,
    )
    output = tmp_path / "offline-frame.3mf"
    request = generation.GenerationRequest(
        route=route,
        frame=frame,
        output_path=output,
        include_base=True,
        include_route=True,
        include_roads=True,
        include_buildings=True,
        route_width_mm=1.2,
        route_height_mm=2.0,
        base_thickness_mm=1.0,
        config=load_config(),
        roads_file=FIXTURES / "frame_roads.geojson",
        buildings_file=FIXTURES / "frame_buildings.geojson",
    )

    progress: list[int] = []
    result = generation.generate_memory_map(
        request, progress_callback=lambda value, *_message: progress.append(value)
    )

    assert result.output_path == output
    assert output.is_file()
    assert progress and progress[-1] == 100
    assert result.stats["roads"] > 0
    assert result.stats["buildings"] == 2  # third footprint is entirely east of the frame
    height_distribution = result.stats["building_height_distribution"]
    assert height_distribution["count"] == 2
    assert sum(height_distribution["height_sources"].values()) == 2
    assert height_distribution["rendered_height_mm"]["maximum"] > 0.0
    assert result.stats["route_height_mm"] == pytest.approx(2.0)
    assert result.stats["route_height_source"] == "explicit"

    names, vertices = _model_objects(output)
    assert {"Base_White", "Route_Accent", "Roads_Black", "Buildings_Verification"} <= names
    assert _model_materials(output) == {
        "Base_White": "#FFFFFFFF",
        "Route_Accent": "#FF6633FF",
        "Roads_Black": "#000000FF",
        "Buildings_Verification": "#808080FF",
    }
    assert _model_assembly(output) == (
        "MemoryMap",
        ["Base_White", "Route_Accent", "Roads_Black", "Buildings_Verification"],
    )
    assert vertices
    # Base may occupy the full physical dimensions; no generated overlay may expand it.
    tolerance = 1e-5
    assert min(x for x, _, _ in vertices) >= -tolerance
    assert min(y for _, y, _ in vertices) >= -tolerance
    assert max(x for x, _, _ in vertices) <= frame.print_width_mm + tolerance
    assert max(y for _, y, _ in vertices) <= frame.print_height_mm + tolerance
    assert result.route_mesh is not None
    assert result.roads_mesh is not None
    assert result.buildings_mesh is not None
    embed_depth = request.config["feature_embed_depth"]
    assert result.route_mesh.bounds[0, 2] == pytest.approx(-embed_depth, abs=tolerance)
    assert result.roads_mesh.bounds[0, 2] == pytest.approx(-embed_depth, abs=tolerance)
    assert result.buildings_mesh.bounds[0, 2] == pytest.approx(-embed_depth, abs=tolerance)
    assert result.route_mesh.bounds[1, 2] == pytest.approx(request.route_height_mm, abs=tolerance)
    assert result.roads_mesh.bounds[1, 2] == pytest.approx(
        request.config["road_height"], abs=tolerance
    )
    assert 0.0 < result.buildings_mesh.bounds[1, 2]
    assert result.buildings_mesh.bounds[1, 2] <= (
        request.config["max_print_height_mm"] + tolerance
    )
