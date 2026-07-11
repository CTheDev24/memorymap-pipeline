import logging
logging.basicConfig(level=logging.WARNING)

from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform
from memorymap_pipeline.roads import download_and_build_roads
import numpy as np

route = load_route_from_gpx("Sample_Run.gpx")
center_lat = route.points[0].latitude
center_lon = route.points[0].longitude

# Get bbox
latitudes = [p.latitude for p in route.points]
longitudes = [p.longitude for p in route.points]
lat_min, lat_max = min(latitudes), max(latitudes)
lon_min, lon_max = min(longitudes), max(longitudes)
bbox = (lat_min, lat_max, lon_min, lon_max)

# Transform
projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
route_span_x = float(projected[:, 0].max() - projected[:, 0].min())
route_span_y = float(projected[:, 1].max() - projected[:, 1].min())
orientation = "landscape" if route_span_x > route_span_y else "portrait"
map_width = 240.0 if orientation == "landscape" else 190.0
map_height = 190.0 if orientation == "landscape" else 240.0
margin_mm = 8.0
transform = compute_normalize_center_transform(projected, map_width, map_height, margin_mm)

print("Fetching roads with radius_m=200...")
print(f"Map bounds: {map_width}x{map_height}mm")
print(f"Clip box: ({margin_mm}, {margin_mm}) to ({map_width-margin_mm}, {map_height-margin_mm})")
print()

unioned, roads_mesh = download_and_build_roads(
    bbox=bbox,
    center_lat=center_lat,
    center_lon=center_lon,
    transform=transform,
    road_types=["motorway", "trunk", "primary", "secondary", "tertiary", "residential"],
    road_widths={"motorway": 2.4, "trunk": 2.0, "primary": 2.0, "secondary": 1.6, "tertiary": 1.4, "residential": 1.1},
    road_height_mm=0.8,
    map_width_mm=map_width,
    map_height_mm=map_height,
    margin_mm=margin_mm,
    radius_m=200,
)

if unioned is not None:
    bounds = unioned.bounds
    print(f"ROADS BOUNDS (mm): X=[{bounds[0]:.2f}, {bounds[2]:.2f}], Y=[{bounds[1]:.2f}, {bounds[3]:.2f}]")
    print(f"Expected to span: ~{route_span_x*3.27:.0f}mm X, ~{route_span_y*3.27:.0f}mm Y")
else:
    print("No roads data")
