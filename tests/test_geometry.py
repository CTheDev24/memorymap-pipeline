import numpy as np
from shapely.geometry import LineString

from memorymap_pipeline.geometry import buffered_polygon_from_points
from memorymap_pipeline.mesh import route_mesh_from_polygon


def test_dense_gpx_buffer_is_simplified_before_extrusion():
    x = np.linspace(5.0, 235.0, 4_000)
    y = 95.0 + 18.0 * np.sin(x / 8.0) + 0.003 * np.sin(x * 40.0)
    points = np.column_stack((x, y))
    raw = LineString(points).buffer(
        0.6,
        resolution=16,
        cap_style=2,
        join_style=1,
    )

    route = buffered_polygon_from_points(points, 1.2)
    mesh = route_mesh_from_polygon(route, 2.2, -0.2)

    assert len(route.exterior.coords) < len(raw.exterior.coords)
    assert mesh.is_watertight
