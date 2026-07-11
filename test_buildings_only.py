import logging
logging.basicConfig(level=logging.INFO)

from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform
from memorymap_pipeline.buildings import download_and_build_buildings
from memorymap_pipeline.mesh import export_3mf, build_base_plate

route = load_route_from_gpx("Sample_Run.gpx")
all_lats = [p.latitude for p in route.points]
all_lons = [p.longitude for p in route.points]
center_lat = (min(all_lats) + max(all_lats)) / 2.0
center_lon = (min(all_lons) + max(all_lons)) / 2.0

# Project and transform
projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
route_span_x = float(projected[:, 0].max() - projected[:, 0].min())
route_span_y = float(projected[:, 1].max() - projected[:, 1].min())
orientation = "landscape" if route_span_x > route_span_y else "portrait"
map_width = 240.0 if orientation == "landscape" else 190.0
map_height = 190.0 if orientation == "landscape" else 240.0
transform = compute_normalize_center_transform(projected, map_width, map_height, 8.0)

print("Downloading buildings...")
unioned_buildings, buildings_mesh = download_and_build_buildings(
    bbox=(min(all_lats), max(all_lats), min(all_lons), max(all_lons)),
    center_lat=center_lat,
    center_lon=center_lon,
    transform=transform,
    map_width_mm=map_width,
    map_height_mm=map_height,
    margin_mm=8.0,
    debug=False,
)

if unioned_buildings is not None:
    b = unioned_buildings.bounds
    print(f"Buildings found: X=[{b[0]:.1f}, {b[2]:.1f}]mm, Y=[{b[1]:.1f}, {b[3]:.1f}]mm")
    print(f"Buildings mesh vertices: {len(buildings_mesh.vertices) if buildings_mesh else 0}")
else:
    print("No buildings data!")

if buildings_mesh:
    # Create base plate for reference
    base_mesh = build_base_plate(map_width, map_height, thickness_mm=1.0)
    
    # Export
    export_3mf("test_buildings_only.3mf", base_mesh, None, None, buildings_mesh)
    print("\nExported test_buildings_only.3mf")
    print("This file has only base plate + buildings (no route, no roads)")
else:
    print("No buildings mesh to export")
