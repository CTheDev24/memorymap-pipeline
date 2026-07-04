from pathlib import Path

from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import normalize_and_scale_points, project_points


def test_parse_and_scale_route(tmp_path: Path) -> None:
    gpx_text = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<gpx version=\"1.1\" creator=\"test\">
  <trk>
    <trkseg>
      <trkpt lat=\"40.7128\" lon=\"-74.0060\"><ele>10</ele></trkpt>
      <trkpt lat=\"40.7129\" lon=\"-74.0061\"><ele>11</ele></trkpt>
      <trkpt lat=\"40.7130\" lon=\"-74.0062\"><ele>12</ele></trkpt>
    </trkseg>
  </trk>
</gpx>
"""
    gpx_path = tmp_path / "sample.gpx"
    gpx_path.write_text(gpx_text, encoding="utf-8")

    route = load_route_from_gpx(gpx_path)
    assert len(route.points) == 3

    projected = project_points(route.points, center_lat=route.points[0].latitude, center_lon=route.points[0].longitude)
    scaled = normalize_and_scale_points(projected, width_mm=241.0, height_mm=190.0)

    assert scaled.shape == (3, 2)
    assert scaled[:, 0].min() >= 0.0
    assert scaled[:, 0].max() <= 241.0
    assert scaled[:, 1].min() >= 0.0
    assert scaled[:, 1].max() <= 190.0
