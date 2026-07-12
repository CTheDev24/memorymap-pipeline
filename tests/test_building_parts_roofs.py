from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest
from shapely.geometry import box

from memorymap_pipeline.buildings import _building_dimensions, _overpass_geometry, _roof_mesh
from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame


DEFAULTS = dict(default_height_m=6.0, levels_to_m=3.0, max_height_m=400.0)


def test_dimensions_include_roof_above_building_levels() -> None:
    dims = _building_dimensions(
        {"building:levels": "3", "roof:height": "2.5", "roof:shape": "gabled"}, **DEFAULTS
    )
    assert dims.eave_height_m == pytest.approx(9.0)
    assert dims.total_height_m == pytest.approx(11.5)
    assert dims.roof_height_m == pytest.approx(2.5)


def test_explicit_height_includes_roof_and_min_height() -> None:
    dims = _building_dimensions(
        {"height": "20", "min_height": "6", "roof:height": "4", "roof:shape": "hipped"}, **DEFAULTS
    )
    assert dims.min_height_m == pytest.approx(6.0)
    assert dims.eave_height_m == pytest.approx(16.0)
    assert dims.total_height_m == pytest.approx(20.0)


@pytest.mark.parametrize("shape", ["gabled", "hipped", "pyramidal", "skillion"])
def test_supported_roofs_reach_tagged_height(shape: str) -> None:
    mesh = _roof_mesh(box(0, 0, 20, 10), eave_z=5.0, roof_height_mm=3.0, shape=shape)
    assert mesh is not None
    assert mesh.bounds[0, 2] == pytest.approx(5.0)
    assert mesh.bounds[1, 2] == pytest.approx(8.0)


def test_unknown_roof_shape_falls_back_to_flat() -> None:
    dims = _building_dimensions(
        {"height": "10", "roof:height": "3", "roof:shape": "onion"}, **DEFAULTS
    )
    assert dims.roof_shape == "flat"


def test_gabled_roof_orientation_across_rotates_the_ridge() -> None:
    polygon = box(0, 0, 20, 10)
    along = _roof_mesh(polygon, 5.0, 3.0, "gabled", orientation="along")
    across = _roof_mesh(polygon, 5.0, 3.0, "gabled", orientation="across")
    assert along is not None and across is not None
    along_peak = along.vertices[np.isclose(along.vertices[:, 2], 8.0)]
    across_peak = across.vertices[np.isclose(across.vertices[:, 2], 8.0)]
    assert len(set(along_peak[:, 0])) > len(set(along_peak[:, 1]))
    assert len(set(across_peak[:, 1])) > len(set(across_peak[:, 0]))


def test_overpass_multipolygon_preserves_centerpoint_style_crown_hole() -> None:
    def points(coords):
        return [{"lon": x, "lat": y} for x, y in coords]

    element = {
        "type": "relation",
        "members": [
            {
                "type": "way",
                "role": "outer",
                "geometry": points([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]),
            },
            {
                "type": "way",
                "role": "inner",
                "geometry": points([(3, 3), (7, 3), (7, 7), (3, 7), (3, 3)]),
            },
        ],
    }
    polygon = _overpass_geometry(element)
    assert polygon is not None
    assert polygon.area == pytest.approx(84.0)
    assert len(polygon.interiors) == 1


def test_offline_parts_fixture_generates_embedded_colored_layer(tmp_path: Path) -> None:
    fixtures = Path(__file__).parent / "fixtures"
    route = load_route_from_gpx(fixtures / "frame_route.gpx")
    frame = MapFrame(
        center_lat=29.76025,
        center_lon=-95.37,
        coverage_width_m=180.0,
        coverage_height_m=140.0,
        print_width_mm=190,
        print_height_mm=240,
        margin_mm=8,
    )
    result = generate_memory_map(
        GenerationRequest(
            route=route,
            frame=frame,
            output_path=tmp_path / "parts.3mf",
            include_base=True,
            include_route=False,
            include_roads=False,
            include_buildings=True,
            buildings_file=fixtures / "building_parts_roofs.geojson",
        )
    )
    assert result.buildings_mesh is not None
    assert result.buildings_mesh.bounds[0, 2] == pytest.approx(-0.2)
    assert result.buildings_mesh.bounds[1, 2] == pytest.approx(31.75)
    assert result.output_path.exists()
    with zipfile.ZipFile(result.output_path) as archive:
        model_name = next(name for name in archive.namelist() if name.endswith(".model"))
        root = ET.fromstring(archive.read(model_name))
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    objects = {node.attrib["id"]: node for node in root.findall("m:resources/m:object", ns)}
    buildings = next(
        node for node in objects.values() if node.attrib.get("name") == "Buildings_Verification"
    )
    palette = root.find(f"m:resources/m:basematerials[@id='{buildings.attrib['pid']}']", ns)
    assert palette is not None
    assert palette[int(buildings.attrib["pindex"])].attrib["displaycolor"] == "#808080FF"
    build_item = root.find("m:build/m:item", ns)
    assert build_item is not None
    assembly = objects[build_item.attrib["objectid"]]
    assert buildings.attrib["id"] in {
        component.attrib["objectid"]
        for component in assembly.findall("m:components/m:component", ns)
    }
