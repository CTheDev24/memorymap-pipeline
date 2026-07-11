import matplotlib.pyplot as plt
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform, apply_transform
from memorymap_pipeline.geometry import buffered_polygon_from_points
import numpy as np

route = load_route_from_gpx("Sample_Run.gpx")
center_lat = route.points[0].latitude
center_lon = route.points[0].longitude

# Project and transform
projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
route_span_x = float(projected[:, 0].max() - projected[:, 0].min())
route_span_y = float(projected[:, 1].max() - projected[:, 1].min())
orientation = "landscape" if route_span_x > route_span_y else "portrait"
map_width = 240.0 if orientation == "landscape" else 190.0
map_height = 190.0 if orientation == "landscape" else 240.0

transform = compute_normalize_center_transform(projected, width_mm=map_width, height_mm=map_height, margin_mm=8.0)
scaled = apply_transform(projected, transform)

# Create buffered route
route_width_mm = 1.2
poly = buffered_polygon_from_points(scaled, route_width_mm)

# Visualization
fig, ax = plt.subplots(figsize=(10, 8))

# Draw map bounds
ax.add_patch(plt.Rectangle((0, 0), map_width, map_height, fill=False, edgecolor='black', linewidth=2, label=f'Map bounds ({map_width}x{map_height}mm)'))
ax.add_patch(plt.Rectangle((8, 8), map_width-16, map_height-16, fill=False, edgecolor='gray', linewidth=1, linestyle='--', label='Printable area'))

# Draw route
if poly and not poly.is_empty:
    x, y = poly.exterior.xy
    ax.fill(x, y, alpha=0.5, fc='green', ec='darkgreen', linewidth=1, label='Route path')

# Draw route points (sample every 20 points for clarity)
ax.scatter(scaled[::20, 0], scaled[::20, 1], c='red', s=10, zorder=5, alpha=0.6)

# Route stats
print(f"Route: Sample_Run.gpx (Houston)")
print(f"Map orientation: {orientation} ({map_width}x{map_height}mm)")
print(f"Total route points: {len(route.points)}")
print(f"Route X span: {scaled[:, 0].max() - scaled[:, 0].min():.1f}mm")
print(f"Route Y span: {scaled[:, 1].max() - scaled[:, 1].min():.1f}mm")

ax.set_xlim(-10, map_width+10)
ax.set_ylim(-10, map_height+10)
ax.set_aspect('equal', adjustable='box')
ax.set_xlabel('X (mm)')
ax.set_ylabel('Y (mm)')
ax.set_title('Houston Route Visualization')
ax.legend(loc='upper right', fontsize=8)
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig('houston_visualization.png', dpi=100)
print("\nSaved houston_visualization.png")
plt.close()
