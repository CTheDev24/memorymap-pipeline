import json

import numpy as np
import pytest

from memorymap_pipeline.config import load_config
from memorymap_pipeline.gpx_loader import RoutePoint
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.print_scale import PRINT_SCALE_DEFAULTS, PrintScaleContext


@pytest.mark.parametrize(
    "coverage,size,expected",
    [
        ((1800, 2300), (190, 240), 0.1),
        ((2300, 1800), (240, 190), 0.1),
        ((1000, 20900), (190, 240), 230 / 20900),
        ((4000, 1000), (190, 240), 180 / 4000),
        ((1000, 4000), (190, 240), 230 / 4000),
    ],
)
def test_authoritative_scale_and_axis_transform(coverage, size, expected):
    frame = MapFrame(29.76, -95.37, *coverage, *size, margin_mm=5)
    context = PrintScaleContext.from_frame(frame, {})
    assert context.mm_per_meter == pytest.approx(expected)
    assert context.meters_per_mm == pytest.approx(1 / expected)
    points = np.array([[0, 0], [1, 0], [0, 1]])
    delta = frame.transform_projected(points)[1:] - frame.transform_projected(points)[0]
    assert delta == pytest.approx(np.array([[frame.x_mm_per_meter, 0], [0, frame.y_mm_per_meter]]))
    assert context.diagnostics()["anisotropic"] == (
        coverage[0] / coverage[1] != (size[0] - 10) / (size[1] - 10)
    )


@pytest.mark.parametrize("dlat,dlon", [(0.001, 0.04), (0.1, 0.001), (0.05, 0.02)])
def test_route_fit_scale_with_padding_and_rotation(dlat, dlon):
    route = [RoutePoint(29.76, -95.37), RoutePoint(29.76 + dlat, -95.37 + dlon)]
    frame = MapFrame.fit_route(route, 190, 240, 5, rotation_degrees=17, route_padding_mm=0.6)
    assert frame.x_mm_per_meter == pytest.approx(frame.y_mm_per_meter)
    points = frame.transform_points(route)
    extents = np.ptp(points, axis=0)
    assert max(extents / np.array([178.8, 228.8])) == pytest.approx(1)


@pytest.mark.parametrize("line,resolution", [(None, 0.4), (0.45, 0.45)])
def test_printer_thresholds(line, resolution):
    context = PrintScaleContext.from_frame(
        MapFrame(0, 0, 100, 100, 100, 100), {"line_width_mm": line}
    )
    assert context.xy_resolution_mm == resolution
    for name, ratio in [
        ("marginal_width", 1.25),
        ("robust_width", 2),
        ("simplify_tolerance", 0.375),
        ("merge_gap", 1),
        ("group_span", 10),
        ("route_clearance", 2),
    ]:
        assert getattr(context, name + "_mm") == pytest.approx(resolution * ratio)
    assert context.layer_height_mm is None
    assert (
        PrintScaleContext.from_frame(
            MapFrame(0, 0, 100, 100, 100, 100), {"line_width_mm": line, "layer_height_mm": 0.2}
        ).robust_width_mm
        == context.robust_width_mm
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"nozzle_diameter_mm": 0},
        {"line_width_mm": -0.1},
        {"layer_height_mm": 0},
        {"nozzle_diameter_mm": None, "line_width_mm": None},
        {"building_robust_width_ratio": 1},
        {"building_group_span_ratio": 0.5},
        {"building_generalization_mode": "auto"},
        {"line_width_mm": float("inf")},
        {"nozzle_diameter_mm": float("nan")},
        {"nozzle_diameter_mm": True},
        *[{key: -1} for key in PRINT_SCALE_DEFAULTS if key.endswith("_ratio")],
    ],
)
def test_config_rejects_invalid_values(tmp_path, overrides):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(overrides))
    with pytest.raises(ValueError):
        load_config(path)
    with pytest.raises(ValueError):
        PrintScaleContext.from_frame(MapFrame(0, 0, 100, 100, 100, 100), overrides)


def test_legacy_config_loads_with_advisory_defaults(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"building_grouping_width_mm": 1.1, "route_layer_height_mm": 0.12}))
    config = load_config(path)
    assert config["building_generalization_mode"] == "manual"
    assert config["building_grouping_width_mm"] == 1.1
    assert config["layer_height_mm"] is None
    assert config["route_layer_height_mm"] == 0.12
    context = PrintScaleContext.from_frame(MapFrame(0, 0, 100, 100, 100, 100), config)
    assert context.robust_width_mm == 0.8


def test_line_width_can_supply_resolution_without_nozzle():
    context = PrintScaleContext.from_frame(
        MapFrame(0, 0, 100, 100, 100, 100), {"nozzle_diameter_mm": None, "line_width_mm": 0.45}
    )
    assert context.xy_resolution_mm == 0.45


def test_legacy_cli_context_reads_actual_transform():
    from memorymap_pipeline.projection import apply_transform, compute_normalize_center_transform

    points = np.array([[0, 0], [100, 200]])
    transform = compute_normalize_center_transform(points, 190, 240, 5)
    context = PrintScaleContext.from_transform(transform, {"line_width_mm": 0.45})
    assert context.mm_per_meter == transform["scale"]
    assert np.ptp(apply_transform(points, transform), axis=0) == pytest.approx(
        np.ptp(points, axis=0) * context.mm_per_meter
    )
    assert context.xy_resolution_mm == 0.45


def test_custom_ratios_and_legacy_transform_validation():
    config = {
        key: value * 2 for key, value in PRINT_SCALE_DEFAULTS.items() if key.endswith("_ratio")
    }
    frame = MapFrame(0, 0, 100, 100, 100, 100)
    context = PrintScaleContext.from_frame(frame, config)
    default = PrintScaleContext.from_frame(frame, {})
    for name in [
        "marginal_width",
        "robust_width",
        "simplify_tolerance",
        "merge_gap",
        "group_span",
        "route_clearance",
    ]:
        assert getattr(context, name + "_mm") == 2 * getattr(default, name + "_mm")
    assert PrintScaleContext.from_transform({"map_frame": frame}, config) == context
    with pytest.raises(ValueError):
        PrintScaleContext.from_transform({"scale": 0}, {})
