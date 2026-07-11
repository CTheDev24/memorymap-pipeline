import logging
logging.basicConfig(level=logging.WARNING)

from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform
from memorymap_pipeline.roads import download_and_build_roads

route = load_route_from_gpx("Evening_run.gpx")
center_lat = route.points[0].latitude
center_lon = route.points[0].longitude

# Get route bounds
latitudes = [p.latitude for p in route.points]
longitudes = [p.longitude for p in route.points]
lat_min, lat_max = min(latitudes), max(latitudes)
lon_min, lon_max = min(longitudes), max(longitudes)
bbox = (lat_min, lat_max, lon_min, lon_max)

# Compute transform
projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
transform = compute_normalize_center_transform(projected, 190.0, 240.0, 8.0)

print("Fetching roads data...")
unioned, roads_mesh = download_and_build_roads(
    bbox=bbox,
    center_lat=center_lat,
    center_lon=center_lon,
    transform=transform,
    road_types=["motorway", "trunk", "primary", "secondary", "tertiary", "residential"],
    road_widths={"motorway": 2.4, "trunk": 2.0, "primary": 2.0, "secondary": 1.6, "tertiary": 1.4, "residential": 1.1},
    road_height_mm=0.8,
    debug=False,
    radius_m=1000,
)

if unioned is not None:
    bounds = unioned.bounds
    print(f"\nROADS GEOMETRY BOUNDS (mm):")
    print(f"  X: {bounds[0]:.2f} to {bounds[2]:.2f}mm (span: {bounds[2]-bounds[0]:.2f}mm)")
    print(f"  Y: {bounds[1]:.2f} to {bounds[3]:.2f}mm (span: {bounds[3]-bounds[1]:.2f}mm)")
    print(f"\nMAP BOUNDS: 0-190 x 0-240 mm")
    print(f"\nEXCEEDS BOUNDS:")
    print(f"  Left (X<0): {bounds[0] < 0}")
    print(f"  Right (X>190): {bounds[2] > 190}")
    print(f"  Bottom (Y<0): {bounds[1] < 0}")
    print(f"  Top (Y>240): {bounds[3] > 240}")
else:
    print("No roads data retrieved")
