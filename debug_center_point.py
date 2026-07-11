from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, apply_transform, compute_normalize_center_transform
import numpy as np

route = load_route_from_gpx("Sample_Run.gpx")
center_lat = route.points[0].latitude
center_lon = route.points[0].longitude

print(f"Route center point (used for projection): {center_lat:.6f}, {center_lon:.6f}")
print(f"This is the FIRST point of the route, not the geographic center!")

# Calculate actual route center
all_lats = [p.latitude for p in route.points]
all_lons = [p.longitude for p in route.points]
actual_center_lat = (min(all_lats) + max(all_lats)) / 2
actual_center_lon = (min(all_lons) + max(all_lons)) / 2
print(f"\nActual route geographic center: {actual_center_lat:.6f}, {actual_center_lon:.6f}")
print(f"Difference: {abs(center_lat - actual_center_lat):.6f}° lat, {abs(center_lon - actual_center_lon):.6f}° lon")

# Project route
projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
print(f"\nRoute projected around first point:")
print(f"  X: {projected[:, 0].min():.2f} to {projected[:, 0].max():.2f} m")
print(f"  Y: {projected[:, 1].min():.2f} to {projected[:, 1].max():.2f} m")
print(f"  First point at: ({projected[0, 0]:.2f}, {projected[0, 1]:.2f})")

# Show what happens if we center on actual center
projected_centered = project_points(route.points, center_lat=actual_center_lat, center_lon=actual_center_lon)
print(f"\nRoute projected around geographic center:")
print(f"  X: {projected_centered[:, 0].min():.2f} to {projected_centered[:, 0].max():.2f} m")
print(f"  Y: {projected_centered[:, 1].min():.2f} to {projected_centered[:, 1].max():.2f} m")

print("\n>>> Using the first point as center is causing an asymmetric projection!")
