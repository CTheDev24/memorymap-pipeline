import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest
from shapely.geometry import Polygon, box, shape

from memorymap_pipeline.buildings import (
    OVERPASS_TIMEOUT,
    _building_dimensions,
    _configure_overpass,
    _overpass_geometry,
    _roof_mesh,
    download_and_build_buildings,
)
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


def test_building_part_trusts_explicit_height_over_parent_level_count() -> None:
    dims = _building_dimensions(
        {
            "building:part": "yes",
            "height": "40",
            "building:levels": "56",
            "roof:height": "16",
            "roof:shape": "gabled",
        },
        **DEFAULTS,
    )

    assert dims.total_height_m == pytest.approx(40.0)
    assert dims.eave_height_m == pytest.approx(24.0)
    assert dims.roof_height_m == pytest.approx(16.0)


def test_tc_energy_style_fixture_retains_three_solid_pointed_crowns() -> None:
    fixture = Path(__file__).parent / "fixtures" / "tc_energy_style_crowns.geojson"
    features = json.loads(fixture.read_text(encoding="utf-8"))["features"]

    # The source landmark tags currently report 40 m alongside 56 levels.
    # A sub-metre average storey is implausible, so the level-derived height
    # is the narrowest data-driven fallback and avoids flattening the tower.
    tower = _building_dimensions(features[0]["properties"], **DEFAULTS)
    assert tower.total_height_m == pytest.approx(168.0)

    crown_meshes = []
    for feature in features[1:]:
        dimensions = _building_dimensions(feature["properties"], **DEFAULTS)
        crown = _roof_mesh(
            shape(feature["geometry"]),
            eave_z=5.0,
            roof_height_mm=3.0,
            shape=dimensions.roof_shape,
        )
        assert crown is not None
        assert crown.is_watertight
        assert crown.volume > 0.0
        assert crown.bounds[:, 2] == pytest.approx([5.0, 8.0])
        assert np.count_nonzero(np.isclose(crown.vertices[:, 2], 8.0)) == 1
        assert len(np.unique(np.round(crown.vertices[:, 2], 6))) > 2
        crown_meshes.append(crown)

    assert len(crown_meshes) == 3

@pytest.mark.parametrize("shape", ["gabled", "hipped", "pyramidal", "skillion"])
def test_supported_roofs_reach_tagged_height(shape: str) -> None:
    mesh = _roof_mesh(box(0, 0, 20, 10), eave_z=5.0, roof_height_mm=3.0, shape=shape)
    assert mesh is not None
    assert mesh.bounds[0, 2] == pytest.approx(5.0)
    assert mesh.bounds[1, 2] == pytest.approx(8.0)
    assert mesh.is_watertight
    assert mesh.volume > 0.0
    assert np.all(np.bincount(mesh.edges_unique_inverse) == 2)


def test_roof_base_overlaps_supporting_body_without_changing_peak() -> None:
    mesh = _roof_mesh(
        box(0, 0, 20, 10),
        eave_z=5.0,
        roof_height_mm=3.0,
        shape="gabled",
        base_overlap_mm=0.2,
    )

    assert mesh is not None
    assert mesh.is_watertight
    assert mesh.bounds[:, 2] == pytest.approx([4.8, 8.0])


def test_roof_solid_preserves_polygon_inner_ring() -> None:
    polygon = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10)],
        holes=[[(3, 3), (7, 3), (7, 7), (3, 7)]],
    )
    mesh = _roof_mesh(polygon, eave_z=5.0, roof_height_mm=3.0, shape="skillion")
    assert mesh is not None
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(polygon.area * 1.5, rel=1e-6)
    assert np.all(np.bincount(mesh.edges_unique_inverse) == 2)


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
    assert 0.0 < result.buildings_mesh.bounds[1, 2] <= 25.0
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


def test_supported_elevated_crown_keeps_open_sides(tmp_path: Path) -> None:
    buildings_file = tmp_path / "open-crown.geojson"
    outer = [
        [-95.3704, 29.7597],
        [-95.3696, 29.7597],
        [-95.3696, 29.7603],
        [-95.3704, 29.7603],
        [-95.3704, 29.7597],
    ]
    inner = [
        [-95.3702, 29.75985],
        [-95.3702, 29.76015],
        [-95.3698, 29.76015],
        [-95.3698, 29.75985],
        [-95.3702, 29.75985],
    ]
    buildings_file.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "building:part": "yes",
                            "height": 219.8,
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [outer],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {
                            "building:part": "yes",
                            "min_height": 219.8,
                            "height": 225.9,
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [outer, inner],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    frame = MapFrame(
        center_lat=29.7600,
        center_lon=-95.3700,
        coverage_width_m=160.0,
        coverage_height_m=140.0,
        print_width_mm=120.0,
        print_height_mm=100.0,
        margin_mm=5.0,
    )

    _footprints, mesh = download_and_build_buildings(
        bbox=None,
        center_lat=frame.center_lat,
        center_lon=frame.center_lon,
        transform={"map_frame": frame},
        map_width_mm=frame.print_width_mm,
        map_height_mm=frame.print_height_mm,
        margin_mm=frame.margin_mm,
        buildings_file=str(buildings_file),
        embed_depth_mm=0.2,
        extend_elevated_parts_to_ground=True,
    )

    assert mesh is not None
    components = list(mesh.split(only_watertight=False))
    crown = max(components, key=lambda component: component.bounds[1, 2])
    assert crown.bounds[0, 2] > 10.0
    assert crown.bounds[1, 2] - crown.bounds[0, 2] < 2.0
    assert len(crown.split()) == 1
    assert crown.is_watertight


def test_daikin_registry_recipe_is_used_in_full_generation(tmp_path: Path) -> None:
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
            output_path=tmp_path / "daikin.3mf",
            include_base=True,
            include_route=False,
            include_roads=False,
            include_buildings=True,
            buildings_file=fixtures / "daikin_park_landmark.geojson",
            config={"terrain_enabled": False},
        )
    )

    assert result.buildings_mesh is not None
    assert result.buildings_mesh.bounds[0, 2] == pytest.approx(-0.2)
    # The 6.0 mm closed roof plus its 0.32 mm bands is embedded 0.2 mm
    # into the base, leaving a 6.12 mm maximum model elevation.
    assert result.buildings_mesh.bounds[1, 2] == pytest.approx(6.12)
    assert result.buildings_mesh.is_watertight
    assert result.output_path.exists()

def test_overpass_configuration_supports_osmnx_2_settings() -> None:
    settings = type(
        "Settings",
        (),
        {"overpass_url": "old", "requests_timeout": 180},
    )()
    osmnx = type("Osmnx", (), {"settings": settings})()

    _configure_overpass(osmnx, "https://example.test/interpreter")

    assert settings.overpass_url == "https://example.test/interpreter"
    assert settings.requests_timeout == OVERPASS_TIMEOUT == 20
