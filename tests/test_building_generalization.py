import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from memorymap_pipeline.building_generalization import local_core_width_mm, measure_footprints
from memorymap_pipeline.buildings import download_and_build_buildings
from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.print_scale import PrintScaleContext


def test_width_uses_core_not_bounding_box_or_area():
    courtyard = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10)],
        holes=[[(0.2, 0.2), (9.8, 0.2), (9.8, 9.8), (0.2, 9.8)]],
    )
    assert local_core_width_mm(courtyard) == pytest.approx(0.2, abs=0.0001)
    assert local_core_width_mm(box(0, 0, 0.3, 100)) == pytest.approx(0.3, abs=0.0001)
    assert local_core_width_mm(box(0, 0, 2, 3)) == pytest.approx(2, abs=0.0001)


def test_width_statistics_order_independent_and_empty():
    context = PrintScaleContext.from_frame(MapFrame(0, 0, 100, 100, 100, 100), {})
    polygons = [box(0, 0, w, 3) for w in [0.2, 0.6, 1.0]]
    stats = measure_footprints(polygons, context)
    assert stats == measure_footprints(list(reversed(polygons)), context)
    assert stats["building_width_p10_mm"] == pytest.approx(0.28, abs=0.0001)
    assert stats["building_width_p50_mm"] == pytest.approx(0.6, abs=0.0001)
    assert stats["building_width_p90_mm"] == pytest.approx(0.92, abs=0.0001)
    assert stats["buildings_below_marginal_width"] == 1
    assert stats["buildings_below_robust_width"] == 2
    assert stats["buildings_below_robust_width_percent"] == pytest.approx(200 / 3)
    empty = measure_footprints([], context)
    assert empty["building_width_p50_mm"] is None
    assert empty["buildings_below_robust_width_percent"] is None
    invalid = measure_footprints([Polygon()], context)
    assert invalid["width_measurement_failed_count"] == 1
    assert invalid["measured_building_count"] == 0


@pytest.mark.parametrize("grouping,filter_width", [(False, 0), (True, 0), (True, 0.8), (True, 5)])
def test_diagnostics_do_not_change_grouping_filtering_or_mesh(tmp_path, grouping, filter_width):
    frame = MapFrame(41.88, -87.63, 200, 200, 20, 20)
    features = []
    for i in range(9):
        polygon = box(
            8 + (i % 3) * 0.4, 8 + (i // 3) * 0.4, 8.25 + (i % 3) * 0.4, 8.25 + (i // 3) * 0.4
        )
        xy = np.array(polygon.exterior.coords)
        lat, lon = frame.print_to_lonlat(xy[:, 0], xy[:, 1])
        features.append(
            {
                "type": "Feature",
                "properties": {"id": str(i), "building": "house", "height": 4},
                "geometry": {"type": "Polygon", "coordinates": [list(zip(lon, lat))]},
            }
        )
    path = tmp_path / "buildings.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))

    def run(context):
        records = []
        stats = {}
        union, mesh = download_and_build_buildings(
            bbox=None,
            center_lat=frame.center_lat,
            center_lon=frame.center_lon,
            transform={"map_frame": frame},
            map_width_mm=20,
            map_height_mm=20,
            margin_mm=0,
            buildings_file=str(path),
            diagnostics=records,
            print_scale_context=context,
            generalization_stats=stats,
            residential_min_width_mm=filter_width,
            grouping={"width_mm": 0.8, "gap_mm": 0.4, "span_mm": 4, "max_height_mm": 3}
            if grouping
            else None,
            grouping_barriers=box(8.28, 7, 8.38, 10),
        )
        return union, mesh, records, stats

    before = run(None)
    after = run(PrintScaleContext.from_frame(frame, {"line_width_mm": 0.6}))
    assert before[0].equals_exact(after[0], 0)
    if before[1] is None:
        assert after[1] is None
    else:
        assert np.array_equal(before[1].vertices, after[1].vertices)
        assert np.array_equal(before[1].faces, after[1].faces)
        assert before[1].metadata["building_grouping"] == after[1].metadata["building_grouping"]
    assert [{k: v for k, v in r.items() if k != "disposition"} for r in after[2]] == before[2]
    stats = after[3]
    assert stats["source_building_count"] == 9
    assert stats["measured_building_count"] == 9
    assert (
        sum(
            stats[k]
            for k in [
                "ungrouped_count",
                "grouped_source_count",
                "omitted_count",
                "unresolved_count",
            ]
        )
        == 9
    )
    assert stats["grouped_source_count"] == sum(r["status"] == "grouped" for r in before[2])
    assert stats["omitted_count"] == sum(r["status"] == "omitted" for r in before[2])
    if filter_width == 5:
        assert stats["omitted_count"] == 9
        assert stats["group_count"] == 0


def test_generation_context_uses_effective_border_frame_and_remains_advisory(tmp_path):
    fixtures = Path(__file__).parent / "fixtures"
    route = load_route_from_gpx(fixtures / "frame_route.gpx")
    frame = MapFrame(29.76, -95.37, 200, 200, 190, 240, 5)
    outputs = []
    for line in (None, 0.6):
        outputs.append(
            generate_memory_map(
                GenerationRequest(
                    route=route,
                    frame=frame,
                    output_path=tmp_path / "unused.3mf",
                    export_model=False,
                    include_roads=False,
                    buildings_file=fixtures / "frame_buildings.geojson",
                    config={"line_width_mm": line},
                )
            )
        )
    first, second = outputs
    stats = second.stats["building_generalization"]
    assert stats["mm_per_meter"] == 190 / 200  # default removes physical trim
    assert stats["robust_width_mm"] == 1.2
    assert not stats["thresholds_applied_to_geometry"]
    assert stats["source_building_count"] > 0
    for name, mesh in first.meshes.items():
        assert np.array_equal(mesh.vertices, second.meshes[name].vertices)
        assert np.array_equal(mesh.faces, second.meshes[name].faces)


def test_disabled_buildings_are_not_reported_as_measured(tmp_path):
    fixtures = Path(__file__).parent / "fixtures"
    result = generate_memory_map(
        GenerationRequest(
            route=load_route_from_gpx(fixtures / "frame_route.gpx"),
            frame=MapFrame(29.76, -95.37, 200, 200, 190, 240, 5),
            output_path=tmp_path / "unused",
            export_model=False,
            include_roads=False,
            include_buildings=False,
            config={"flat_border_enabled": True},
        )
    )
    stats = result.stats["building_generalization"]
    assert stats["mm_per_meter"] == 180 / 200
    assert stats["measurement_status"] == "disabled"
    assert "source_building_count" not in stats
