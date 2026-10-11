import xml.etree.ElementTree as ET
import zipfile

import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from memorymap_pipeline.map_styles import EXPERIMENTAL_WIDTHS
from memorymap_pipeline.mesh import build_base_plate, export_3mf
from memorymap_pipeline.roads import _extrude_road_regions
from memorymap_pipeline.street_selection import partition_classes, select_streets


def test_class_intersections_have_single_owner_and_route_clearance():
    sources = {"motorways": [box(0, 9, 20, 11)], "main": [box(9, 0, 11, 20)],
               "local": [box(0, 10, 20, 10.4)], "service": [box(10, 0, 10.4, 20)]}
    route = box(4, 0, 5, 20)
    regions = partition_classes(sources, box(0, 0, 20, 20), route)
    for cls, region in regions.items():
        assert region.intersection(route.buffer(.03)).area < 1e-10
        for other, geometry in regions.items():
            if cls != other:
                assert region.intersection(geometry).area < 1e-10
    assert regions["motorways"].covers(box(9.2, 9.2, 10.8, 10.8))


def test_selection_preserves_sparse_cross_streets_and_protected_route_detail():
    records = [(LineString([(0, y), (20, y)]), "residential", f"street-{y}")
               for y in (0, .1, .2, 5, 10)]
    records += [(LineString([(10, -5), (10, 15)]), "residential", "cross street")]
    keep, stats = select_streets(records, box(0, -.5, 1, .5), EXPERIMENTAL_WIDTHS)
    assert {0, 1, 2, 3, 4, 5} == keep  # close streets are protected near the route
    keep, stats = select_streets(records, None, EXPERIMENTAL_WIDTHS)
    assert len(keep) == 4 and 3 in keep and 4 in keep and 5 in keep
    reversed_keep, reversed_stats = select_streets(list(reversed(records)), None, EXPERIMENTAL_WIDTHS)
    assert {records[i][0].wkb for i in keep} == {
        list(reversed(records))[i][0].wkb for i in reversed_keep}
    assert stats == reversed_stats


def test_named_corridors_are_selected_whole_and_major_roads_are_never_thinned():
    records = [(LineString([(0, 0), (10, 0)]), "residential", "a"),
               (LineString([(10, 0), (20, 0)]), "residential", "a"),
               (LineString([(0, .1), (20, .1)]), "residential", "b"),
               (LineString([(0, .2), (20, .2)]), "primary", "main")]
    keep, stats = select_streets(records, None, EXPERIMENTAL_WIDTHS)
    assert 3 in keep
    assert (0 in keep) == (1 in keep)
    assert stats["retained_count"] + stats["omitted_count"] == len(records)


def test_class_export_has_registered_independent_components_with_shared_color(tmp_path):
    from trimesh.util import concatenate
    sources = {"motorways": [box(0, 9, 20, 10.2)], "main": [box(9, 0, 9.8, 20)],
               "local": [box(0, 12, 20, 12.4)], "service": [box(12, 0, 12.4, 20)]}
    regions = partition_classes(sources, box(0, 0, 20, 20))
    classes = {cls: concatenate(_extrude_road_regions([(region, .8)], hierarchical=True,
               embed_depth_mm=.2, z_offset=-.2)) for cls, region in regions.items()}
    for cls, mesh in classes.items():
        assert mesh.bounds[:, 2] == pytest.approx([-.2, .8])
        assert mesh.volume == pytest.approx(regions[cls].area)
        top = mesh.triangles[mesh.face_normals[:, 2] > .9]
        cap = unary_union([Polygon(triangle[:, :2]) for triangle in top])
        assert cap.symmetric_difference(regions[cls]).area < 1e-8
    output = tmp_path / "classes.3mf"
    export_3mf(output, build_base_plate(20, 20, 1.6), None, road_class_meshes=classes,
               road_class_colors={cls: "#123456" for cls in classes})
    with zipfile.ZipFile(output) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    objects = root.findall("m:resources/m:object", ns)
    assert {item.attrib.get("name") for item in objects} >= {
        "Roads_Motorways", "Roads_Main", "Roads_Local", "Roads_Service"}
    assert "Roads_Black" not in {item.attrib.get("name") for item in objects}
    assert len(root.findall("m:build/m:item", ns)) == 1
    assert len(root.findall(".//m:component", ns)) == 5
    assert sum(base.attrib["displaycolor"] == "#123456FF"
               for base in root.findall(".//m:base", ns)) == 4
