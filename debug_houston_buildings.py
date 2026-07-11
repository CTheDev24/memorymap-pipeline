import logging
logging.basicConfig(level=logging.WARNING)

from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform
from memorymap_pipeline.buildings import download_and_build_buildings
import numpy as np

route = load_route_from_gpx("Sample_Run.gpx")
center_lat = route.points[0].latitude
center_lon = route.points[0].longitude

# Get route bounds
latitudes = [p.latitude for p in route.points]
longitudes = [p.longitude for p in route.points]
lat_min, lat_max = min(latitudes), max(latitudes)
lon_min, lon_max = min(longitudes), max(longitudes)
bbox = (lat_min, lat_max, lon_min, lon_max)

# Project and transform
projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
route_span_x = float(projected[:, 0].max() - projected[:, 0].min())
route_span_y = float(projected[:, 1].max() - projected[:, 1].min())
orientation = "landscape" if route_span_x > route_span_y else "portrait"
map_width = 240.0 if orientation == "landscape" else 190.0
map_height = 190.0 if orientation == "landscape" else 240.0
transform = compute_normalize_center_transform(projected, map_width, map_height, 8.0)

print("Fetching buildings data for Houston...")
unioned_buildings, buildings_mesh = download_and_build_buildings(
    bbox=bbox,
    center_lat=center_lat,
    center_lon=center_lon,
    transform=transform,
    map_width_mm=map_width,
    map_height_mm=map_height,
    margin_mm=8.0,
    debug=False,
)

if unioned_buildings is not None:
    bounds = unioned_buildings.bounds
    print(f"\nBUILDINGS BOUNDS (mm):")
    print(f"  X: {bounds[0]:.2f} to {bounds[2]:.2f}mm")
    print(f"  Y: {bounds[1]:.2f} to {bounds[3]:.2f}mm")
    print(f"\nMAP BOUNDS: 0-{map_width} x 0-{map_height}mm")
    print(f"MARGIN BOUNDS: 8-{map_width-8} x 8-{map_height-8}mm")
    
    exceeds = (bounds[0] < 0 or bounds[2] > map_width or 
               bounds[1] < 0 or bounds[3] > map_height)
    print(f"\nExceeds map bounds: {exceeds}")
    
    if buildings_mesh:
        print(f"Buildings mesh: OK (vertices: {len(buildings_mesh.vertices)})")
    else:
        print(f"Buildings mesh: NONE")
else:
    print("No buildings data retrieved")
