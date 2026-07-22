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
from trimesh.creation import box as mesh_box

generation = pytest.importorskip(
    "memorymap_pipeline.generation",
    reason="frame-aware generation service is implemented in the GUI integration phase",
)

from memorymap_pipeline.config import load_config
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.mesh import build_base_plate, export_3mf, route_mesh_from_polygon


FIXTURES = Path(__file__).parent / "fixtures"


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


def test_export_returns_nonblocking_preflight_report(tmp_path: Path) -> None:
    output = tmp_path / "advisory.3mf"
    base = build_base_plate(40.0, 30.0, 1.6)
    route = mesh_box(extents=(8.0, 0.6, 2.2))
    route.apply_translation((20.0, 15.0, 0.9))

    report = export_3mf(
        output,
        base,
        route,
        print_size_mm=(40.0, 30.0),
        margin_mm=5.0,
        declared_feature_widths_mm={"route": 0.6},
    )

    assert output.is_file()
    assert report.status == "yellow"
    assert any(issue.code == "thin_xy_feature" for issue in report.issues)


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
