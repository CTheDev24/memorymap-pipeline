from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform, apply_transform
from memorymap_pipeline.geometry import buffered_polygon_from_points
import numpy as np

# Load route
route = load_route_from_gpx("Evening_run.gpx")
print(f"Route points: {len(route.points)}")
for i, p in enumerate(route.points):
    print(f"  {i}: lat={p.latitude:.6f}, lon={p.longitude:.6f}")

# Project
center_lat = route.points[0].latitude
center_lon = route.points[0].longitude
print(f"\nCenter: lat={center_lat}, lon={center_lon}")

projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
print(f"\nProjected (meters):")
print(f"  X range: {projected[:, 0].min():.2f} to {projected[:, 0].max():.2f} (span: {projected[:, 0].max() - projected[:, 0].min():.2f})")
print(f"  Y range: {projected[:, 1].min():.2f} to {projected[:, 1].max():.2f} (span: {projected[:, 1].max() - projected[:, 1].min():.2f})")

# Determine orientation
route_span_x = float(projected[:, 0].max() - projected[:, 0].min())
route_span_y = float(projected[:, 1].max() - projected[:, 1].min())
orientation = "landscape" if route_span_x > route_span_y else "portrait"
print(f"\nOrientation: {orientation} (X={route_span_x:.2f}, Y={route_span_y:.2f})")

map_width = 190.0 if orientation == "portrait" else 240.0
map_height = 240.0 if orientation == "portrait" else 190.0
margin_mm = 8.0

# Compute transform
transform = compute_normalize_center_transform(projected, width_mm=map_width, height_mm=map_height, margin_mm=margin_mm)
print(f"\nTransform:")
for k, v in transform.items():
    print(f"  {k}: {v}")

# Apply transform
scaled = apply_transform(projected, transform)
print(f"\nScaled (mm):")
print(f"  X range: {scaled[:, 0].min():.2f} to {scaled[:, 0].max():.2f} (span: {scaled[:, 0].max() - scaled[:, 0].min():.2f})")
print(f"  Y range: {scaled[:, 1].min():.2f} to {scaled[:, 1].max():.2f} (span: {scaled[:, 1].max() - scaled[:, 1].min():.2f})")

# Check buffered polygon
route_width_mm = 1.2
poly = buffered_polygon_from_points(scaled, route_width_mm)
if poly and not poly.is_empty:
    bounds = poly.bounds  # (minx, miny, maxx, maxy)
    print(f"\nBuffered polygon bounds (mm):")
    print(f"  X: {bounds[0]:.2f} to {bounds[2]:.2f}")
    print(f"  Y: {bounds[1]:.2f} to {bounds[3]:.2f}")
    print(f"  Should fit in: X: 0-{map_width}, Y: 0-{map_height}")
    print(f"  Exceeds bounds: X={bounds[0] < 0 or bounds[2] > map_width}, Y={bounds[1] < 0 or bounds[3] > map_height}")
