import logging
logging.basicConfig(level=logging.WARNING)

from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform
from memorymap_pipeline.roads import download_and_build_roads
import numpy as np

route = load_route_from_gpx("Sample_Run.gpx")
all_lats = [p.latitude for p in route.points]
all_lons = [p.longitude for p in route.points]
center_lat = (min(all_lats) + max(all_lats)) / 2.0
center_lon = (min(all_lons) + max(all_lons)) / 2.0

projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
route_span_x = float(projected[:, 0].max() - projected[:, 0].min())
route_span_y = float(projected[:, 1].max() - projected[:, 1].min())
orientation = "landscape" if route_span_x > route_span_y else "portrait"
map_width = 240.0 if orientation == "landscape" else 190.0
map_height = 190.0 if orientation == "landscape" else 240.0
transform = compute_normalize_center_transform(projected, map_width, map_height, 8.0)

print("Testing with radius_m=500...")
unioned, _ = download_and_build_roads(
    bbox=(min(all_lats), max(all_lats), min(all_lons), max(all_lons)),
    center_lat=center_lat, center_lon=center_lon,
    transform=transform,
    road_types=["motorway", "trunk", "primary", "secondary", "tertiary", "residential"],
    road_widths={"motorway": 2.4, "trunk": 2.0, "primary": 2.0, "secondary": 1.6, "tertiary": 1.4, "residential": 1.1},
    road_height_mm=0.8,
    map_width_mm=map_width, map_height_mm=map_height, margin_mm=8.0,
    radius_m=500,
)

if unioned:
    b = unioned.bounds
    print(f"ROADS with 500m radius:")
    print(f"  X=[{b[0]:.1f}, {b[2]:.1f}]mm (span {b[2]-b[0]:.1f}mm)")
    print(f"  Y=[{b[1]:.1f}, {b[3]:.1f}]mm (span {b[3]-b[1]:.1f}mm)")
else:
    print("No roads found")
