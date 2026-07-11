from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform, apply_transform
import logging

logging.basicConfig(level=logging.INFO)

route = load_route_from_gpx("Evening_run.gpx")
center_lat = route.points[0].latitude
center_lon = route.points[0].longitude

projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
transform = compute_normalize_center_transform(projected, 190.0, 240.0, 8.0)

print("ROUTE BOUNDS (meters from center):")
print(f"  X: {projected[:, 0].min():.2f} to {projected[:, 0].max():.2f}m")
print(f"  Y: {projected[:, 1].min():.2f} to {projected[:, 1].max():.2f}m")

print("\nQUERY BBOX (for OSM):")
latitudes = [p.latitude for p in route.points]
longitudes = [p.longitude for p in route.points]
lat_min, lat_max = min(latitudes), max(latitudes)
lon_min, lon_max = min(longitudes), max(longitudes)
print(f"  lat: {lat_min:.6f} to {lat_max:.6f}")
print(f"  lon: {lon_min:.6f} to {lon_max:.6f}")

print("\nROAD WIDTHS (mm):")
road_widths = {
    "motorway": 2.4,
    "trunk": 2.0,
    "primary": 2.0,
    "secondary": 1.6,
    "tertiary": 1.4,
    "residential": 1.1,
}
for hw, width in road_widths.items():
    print(f"  {hw}: {width}mm (buffer radius: {width/2}mm)")

print("\nBUILDING HEIGHTS (mm):")
print(f"  max_print_height_mm: 31.75")
print(f"  min_building_height_mm: 0.4")
print(f"  building_default_height_m: 6.0m")
