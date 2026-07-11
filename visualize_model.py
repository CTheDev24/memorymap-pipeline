import matplotlib.pyplot as plt
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform, apply_transform
from memorymap_pipeline.geometry import buffered_polygon_from_points
import numpy as np

route = load_route_from_gpx("Evening_run.gpx")
center_lat = route.points[0].latitude
center_lon = route.points[0].longitude
projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
transform = compute_normalize_center_transform(projected, width_mm=190.0, height_mm=240.0, margin_mm=8.0)
scaled = apply_transform(projected, transform)

route_width_mm = 1.2
poly = buffered_polygon_from_points(scaled, route_width_mm)

fig, ax = plt.subplots(figsize=(8, 10))
# Draw map bounds
ax.add_patch(plt.Rectangle((0, 0), 190, 240, fill=False, edgecolor='black', linewidth=2, label='Map bounds (190x240mm)'))
ax.add_patch(plt.Rectangle((8, 8), 174, 224, fill=False, edgecolor='gray', linewidth=1, linestyle='--', label='Printable area (margin 8mm)'))

# Draw route
if poly and not poly.is_empty:
    x, y = poly.exterior.xy
    ax.fill(x, y, alpha=0.6, fc='green', ec='darkgreen', linewidth=1.5, label='Route path')

# Draw route points
ax.scatter(scaled[:, 0], scaled[:, 1], c='red', s=20, zorder=5, label='Route points')

ax.set_xlim(-10, 200)
ax.set_ylim(-10, 250)
ax.set_aspect('equal', adjustable='box')
ax.set_xlabel('X (mm)')
ax.set_ylabel('Y (mm)')
ax.set_title('Memory Map Route Visualization (Evening Run)')
ax.legend(loc='upper right')
ax.grid(True, alpha=0.3)
fig.savefig('route_visualization.png', dpi=100, bbox_inches='tight')
print("Saved route_visualization.png")
plt.close()
