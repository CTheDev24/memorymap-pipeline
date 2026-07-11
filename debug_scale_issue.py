import logging
logging.basicConfig(level=logging.WARNING)

from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform, apply_transform
from memorymap_pipeline.geometry import buffered_polygon_from_points
import numpy as np

route = load_route_from_gpx("Sample_Run.gpx")
center_lat = route.points[0].latitude
center_lon = route.points[0].longitude

# Project route
projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
route_span_x = float(projected[:, 0].max() - projected[:, 0].min())
route_span_y = float(projected[:, 1].max() - projected[:, 1].min())
orientation = "landscape" if route_span_x > route_span_y else "portrait"
map_width = 240.0 if orientation == "landscape" else 190.0
map_height = 190.0 if orientation == "landscape" else 240.0

# Compute transform
transform = compute_normalize_center_transform(projected, map_width, map_height, 8.0)

# Scale route
scaled_route = apply_transform(projected, transform)

print("ROUTE DATA:")
print(f"  Projected (meters): X={projected[:, 0].min():.2f} to {projected[:, 0].max():.2f}m, Y={projected[:, 1].min():.2f} to {projected[:, 1].max():.2f}m")
print(f"  Scaled (mm): X={scaled_route[:, 0].min():.2f} to {scaled_route[:, 0].max():.2f}mm, Y={scaled_route[:, 1].min():.2f} to {scaled_route[:, 1].max():.2f}mm")
route_poly = buffered_polygon_from_points(scaled_route, 1.2)
if route_poly and not route_poly.is_empty:
    bounds = route_poly.bounds
    print(f"  Buffered bounds: X=[{bounds[0]:.2f}, {bounds[2]:.2f}] (span {bounds[2]-bounds[0]:.1f}mm), Y=[{bounds[1]:.2f}, {bounds[3]:.2f}] (span {bounds[3]-bounds[1]:.1f}mm)")

print(f"\nTRANSFORM:")
print(f"  scale: {transform['scale']:.6f} mm/m")
print(f"  min_x: {transform['min_x']:.2f} m")
print(f"  min_y: {transform['min_y']:.2f} m")
print(f"  offset_x: {transform['offset_x']:.2f} mm")
print(f"  offset_y: {transform['offset_y']:.2f} mm")

# Check roads query bbox
latitudes = [p.latitude for p in route.points]
longitudes = [p.longitude for p in route.points]
lat_min, lat_max = min(latitudes), max(latitudes)
lon_min, lon_max = min(longitudes), max(longitudes)
print(f"\nROADS/BUILDINGS QUERY BBOX:")
print(f"  lat: {lat_min:.6f} to {lat_max:.6f} (span: {(lat_max-lat_min)*111:.1f}m)")
print(f"  lon: {lon_min:.6f} to {lon_max:.6f} (span: {(lon_max-lon_min)*111*np.cos(np.radians(lat_min)):.1f}m)")

print(f"\nEXPECTED SIZES:")
print(f"  Roads/buildings should span ~{(scaled_route[:, 0].max()-scaled_route[:, 0].min()):.1f}mm X, ~{(scaled_route[:, 1].max()-scaled_route[:, 1].min()):.1f}mm Y")
print(f"\nACTUAL SIZES (from earlier debug):")
print(f"  Roads: 22mm X × 17mm Y")
print(f"  Buildings: 27mm X × 21mm Y")
print(f"\nRATIO (actual/expected):")
print(f"  Roads: {22/(scaled_route[:, 0].max()-scaled_route[:, 0].min()):.2%} of expected")
