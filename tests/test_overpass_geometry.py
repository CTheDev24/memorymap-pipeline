import json
from pathlib import Path

import pytest
from shapely.geometry import Polygon, Point
from memorymap_pipeline.buildings import _overpass_geometry


def member(points, role="outer"):
    return {"type": "way", "role": role, "geometry": [{"lon": x, "lat": y} for x,y in points]}


def test_split_reversed_outer_and_inner_rings():
    element = {"type":"relation", "members": [
        member([(10,10),(10,0),(0,0)]),
        member([(0,0),(0,10),(10,10)]),
        member([(3,3),(3,7),(7,7)], "inner"),
        member([(3,3),(7,3),(7,7)], "inner"),
    ]}
    result = _overpass_geometry(element)
    assert result.is_valid
    assert result.area == pytest.approx(84)
    assert len(result.interiors) == 1


def test_self_crossing_way_retains_both_lobes():
    element = member([(0,0),(2,2),(0,2),(2,0),(0,0)])
    element["type"] = "way"
    result = _overpass_geometry(element)
    assert result.is_valid
    assert result.area == pytest.approx(2)


def test_incomplete_relation_not_artificially_closed(caplog):
    result = _overpass_geometry({"type":"relation", "id":42,
                                "members":[member([(0,0),(10,0),(10,10)])]})
    assert result is None
    assert "relation/42" in caplog.text


def test_la_reported_relation_preserves_courtyard():
    element = json.loads((Path(__file__).parent / "fixtures/la_relation_6100581.json").read_text())
    result = _overpass_geometry(element)
    assert result is not None and result.is_valid
    assert result.geom_type == "Polygon"
    assert len(result.interiors) == 1
    inner = next(m for m in element["members"] if m["role"] == "inner")
    hole = Polygon([(p["lon"],p["lat"]) for p in inner["geometry"]])
    assert result.intersection(hole).area < 1e-15
    assert result.area > hole.area * .1


@pytest.mark.parametrize("malformed", [False, True])
def test_tiled_source_reaches_mesh_or_explicit_audit(monkeypatch, malformed):
    from memorymap_pipeline import building_download
    from memorymap_pipeline.buildings import download_and_build_buildings
    from memorymap_pipeline.building_heights import BuildingHeightPolicy
    from memorymap_pipeline.map_frame import MapFrame
    from memorymap_pipeline.generation import _query_bounds
    from memorymap_pipeline.print_scale import PrintScaleContext
    el = json.loads((Path(__file__).parent / "fixtures/la_relation_6100581.json").read_text())
    if malformed:
        el["members"] = el["members"][:1]
    monkeypatch.setattr(building_download, "download_tiles", lambda *args, **kwargs: [el])
    frame = MapFrame(34.0745375, -118.355342, 22907.793183898, 18135.336270586, 240, 190)
    records = []
    _, mesh = download_and_build_buildings(
        bbox=_query_bounds(frame)[0], radius_m=15000,
        center_lat=frame.center_lat, center_lon=frame.center_lon,
        transform={"map_frame":frame}, map_width_mm=240, map_height_mm=190,
        margin_mm=0, diagnostics=records,
        height_policy=BuildingHeightPolicy("proportional", 2, .8),
        print_scale_context=PrintScaleContext.from_frame(frame, {}),
        generalization_mode="print_optimized",
    )
    assert records and records[0]["source_id"] == "6100581"
    if malformed:
        assert mesh is None
        assert records[0]["status"] == "unresolved"
    else:
        assert mesh is not None and mesh.is_watertight
        assert records[0]["output_face_ranges"]
