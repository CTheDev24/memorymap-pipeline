import logging
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform
from memorymap_pipeline.buildings import download_and_build_buildings

route = load_route_from_gpx("Sample_Run.gpx")
all_lats = [p.latitude for p in route.points]
all_lons = [p.longitude for p in route.points]
center_lat = (min(all_lats) + max(all_lats)) / 2.0
center_lon = (min(all_lons) + max(all_lons)) / 2.0

projected = project_points(route.points, center_lat=center_lat, center_lon=center_lon)
route_span_x = float(projected[:, 0].max() - projected[:, 0].min())
route_span_y = float(projected[:, 1].max() - projected[:, 1].min())
orientation = "landscape" if route_span_x > route_span_y else "portrait"
map_width = 240.0 if orientation == "landscape" else 190.0
map_height = 190.0 if orientation == "landscape" else 240.0
transform = compute_normalize_center_transform(projected, map_width, map_height, 8.0)

print(f"Center: {center_lat}, {center_lon}")
print(f"BBox: lat {min(all_lats):.6f}-{max(all_lats):.6f}, lon {min(all_lons):.6f}-{max(all_lons):.6f}")
print()

unioned_buildings, buildings_mesh = download_and_build_buildings(
    bbox=(min(all_lats), max(all_lats), min(all_lons), max(all_lons)),
    center_lat=center_lat,
    center_lon=center_lon,
    transform=transform,
    map_width_mm=map_width,
    map_height_mm=map_height,
    margin_mm=8.0,
)

print(f"\nResult: unioned_buildings={unioned_buildings is not None}, mesh={buildings_mesh is not None}")
