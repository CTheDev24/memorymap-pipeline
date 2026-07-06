from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import compute_normalize_center_transform
from memorymap_pipeline.roads import download_and_build_roads

route = load_route_from_gpx('sample.gpx')
latitudes = [p.latitude for p in route.points]
longitudes = [p.longitude for p in route.points]
lat_min = min(latitudes)
lat_max = max(latitudes)
lon_min = min(longitudes)
lon_max = max(longitudes)
print('bbox raw:', lat_min, lat_max, lon_min, lon_max)
projected = None
transform = compute_normalize_center_transform(
    projected=__import__('numpy').array([[0,0]]), width_mm=190.0, height_mm=240.0, margin_mm=8.0
)
print('calling download_and_build_roads...')
unioned, mesh = download_and_build_roads(
    bbox=(lat_min, lat_max, lon_min, lon_max),
    center_lat=route.points[0].latitude,
    center_lon=route.points[0].longitude,
    transform={'scale':1.0,'min_x':0.0,'min_y':0.0,'margin_mm':8.0},
    road_types=['motorway','trunk','primary','secondary','tertiary','residential'],
    road_widths={'motorway':2.4,'trunk':2.0,'primary':2.0,'secondary':1.6,'tertiary':1.4,'residential':1.1},
    road_height_mm=0.8,
    debug=True,
    z_offset=1.0,
)
print('done, unioned type:', type(unioned), 'mesh:', type(mesh))
