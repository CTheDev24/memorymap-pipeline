import numpy as np
import pytest

from memorymap_pipeline.gpx_loader import RoutePoint
from memorymap_pipeline.map_frame import MapFrame


def _route():
    return [
        RoutePoint(latitude=40.0, longitude=-74.0),
        RoutePoint(latitude=40.001, longitude=-74.002),
    ]


def test_fit_route_centers_route_inside_printable_area():
    frame = MapFrame.fit_route(_route(), 240.0, 190.0, margin_mm=8.0)
    transformed = frame.transform_points(_route())

    assert transformed[:, 0].min() >= 8.0 - 0.01
    assert transformed[:, 0].max() <= 232.0 + 0.01
    assert transformed[:, 1].min() >= 8.0 - 1e-6
    assert transformed[:, 1].max() <= 182.0 + 1e-6
    assert np.mean(transformed, axis=0) == pytest.approx([120.0, 95.0], abs=0.1)


def test_fit_route_reserves_padding_inside_the_frame():
    frame = MapFrame.fit_route(
        _route(),
        240.0,
        190.0,
        margin_mm=5.0,
        route_padding_mm=6.0,
    )
    transformed = frame.transform_points(_route())

    assert transformed[:, 0].min() >= 11.0 - 0.01
    assert transformed[:, 0].max() <= 229.0 + 0.01
    assert transformed[:, 1].min() >= 11.0 - 0.01
    assert transformed[:, 1].max() <= 179.0 + 0.01


def test_fit_route_rejects_excessive_route_padding():
    with pytest.raises(ValueError, match="Route padding"):
        MapFrame.fit_route(_route(), 100.0, 80.0, route_padding_mm=40.0)


def test_frame_rotation_changes_axis_direction():
    frame = MapFrame(40.0, -74.0, 100.0, 100.0, 100.0, 100.0, rotation_degrees=90.0)
    transformed = frame.transform_projected(np.array([[10.0, 0.0]]))
    assert transformed[0] == pytest.approx([50.0, 40.0])


def test_print_to_lonlat_round_trips_rotated_frame_coordinates():
    frame = MapFrame(
        36.25,
        -121.75,
        20_000.0,
        30_000.0,
        190.0,
        240.0,
        margin_mm=5.0,
        rotation_degrees=27.0,
    )
    x = np.array([5.0, 70.0, 185.0])
    y = np.array([5.0, 125.0, 235.0])

    latitudes, longitudes = frame.print_to_lonlat(x, y)
    restored = frame.transform_lonlat(latitudes, longitudes)

    assert restored == pytest.approx(np.column_stack((x, y)), abs=1e-6)


def test_frame_rejects_invalid_margin():
    with pytest.raises(ValueError, match="Margin"):
        MapFrame(40.0, -74.0, 100.0, 100.0, 100.0, 100.0, margin_mm=50.0)
