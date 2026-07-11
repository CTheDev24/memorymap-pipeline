from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform, apply_transform
from memorymap_pipeline.geometry import buffered_polygon_from_points, validate_polygon, repair_polygon
import numpy as np

print("="*80)
print("COMPREHENSIVE DEBUG - Evening Run GPX")
print("="*80)

# Load route
route = load_route_from_gpx("Evening_run.gpx")
print(f"\n1. ROUTE DATA")
print(f"   Total points: {len(route.points)}")
print(f"   First point: lat={route.points[0].latitude}, lon={route.points[0].longitude}")
print(f"   Last point: lat={route.points[-1].latitude}, lon={route.points[-1].longitude}")

# Check if it's a closed loop
is_closed = (route.points[0].latitude == route.points[-1].latitude and 
             route.points[0].longitude == route.points[-1].longitude)
print(f"   Closed loop: {is_closed}")

# Project
center_lat = route.points[0].latitude
center_lon = route.points[0].longitude
projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)

print(f"\n2. PROJECTED COORDINATES (meters from center)")
print(f"   X range: {projected[:, 0].min():.3f} to {projected[:, 0].max():.3f} m (span: {projected[:, 0].max() - projected[:, 0].min():.3f}m)")
print(f"   Y range: {projected[:, 1].min():.3f} to {projected[:, 1].max():.3f} m (span: {projected[:, 1].max() - projected[:, 1].min():.3f}m)")

# Detect orientation
route_span_x = float(projected[:, 0].max() - projected[:, 0].min())
route_span_y = float(projected[:, 1].max() - projected[:, 1].min())
orientation = "landscape" if route_span_x > route_span_y else "portrait"
map_width = 190.0 if orientation == "portrait" else 240.0
map_height = 240.0 if orientation == "portrait" else 190.0

print(f"\n3. ORIENTATION")
print(f"   X span > Y span: {route_span_x > route_span_y}")
print(f"   Chosen: {orientation} ({map_width}x{map_height}mm)")

# Compute transform
margin_mm = 8.0
transform = compute_normalize_center_transform(projected, width_mm=map_width, height_mm=map_height, margin_mm=margin_mm)

print(f"\n4. TRANSFORM PARAMETERS")
for k, v in transform.items():
    print(f"   {k}: {v:.6f}")

# Apply transform
scaled = apply_transform(projected, transform)

print(f"\n5. SCALED COORDINATES (mm on map)")
print(f"   X range: {scaled[:, 0].min():.2f} to {scaled[:, 0].max():.2f} mm")
print(f"   Y range: {scaled[:, 1].min():.2f} to {scaled[:, 1].max():.2f} mm")
print(f"   Map bounds: 0-{map_width}x0-{map_height}mm")
print(f"   Margin bounds: {margin_mm}-{map_width-margin_mm}x{margin_mm}-{map_height-margin_mm}mm")

# Check bounds violations
x_min, x_max = scaled[:, 0].min(), scaled[:, 0].max()
y_min, y_max = scaled[:, 1].min(), scaled[:, 1].max()
x_exceeds = x_min < 0 or x_max > map_width
y_exceeds = y_min < 0 or y_max > map_height

print(f"\n6. BOUNDS CHECK")
print(f"   X exceeds: {x_exceeds} (min={x_min:.2f}, max={x_max:.2f})")
print(f"   Y exceeds: {y_exceeds} (min={y_min:.2f}, max={y_max:.2f})")

# Create buffered polygon
route_width_mm = 1.2
poly = buffered_polygon_from_points(scaled, route_width_mm)

print(f"\n7. BUFFERED POLYGON")
print(f"   Route width: {route_width_mm}mm")
print(f"   Polygon type: {poly.geom_type}")
print(f"   Is valid: {poly.is_valid}")
print(f"   Is empty: {poly.is_empty}")

if not poly.is_empty:
    bounds = poly.bounds  # (minx, miny, maxx, maxy)
    poly_area = poly.area
    print(f"   Bounds: x=[{bounds[0]:.2f}, {bounds[2]:.2f}], y=[{bounds[1]:.2f}, {bounds[3]:.2f}]")
    print(f"   Area: {poly_area:.2f} mm²")
    
    # Check if invalid
    if not poly.is_valid:
        is_valid, explanation = validate_polygon(poly)
        print(f"   Validity: {explanation}")
        repaired, repaired_valid, repaired_explanation = repair_polygon(poly)
        print(f"   Repaired: {repaired_valid} ({repaired_explanation})")

print("\n" + "="*80)
