import logging
logging.basicConfig(level=logging.INFO)

from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.projection import project_points, compute_normalize_center_transform, apply_transform, project_lonlat_array
import shapely.geometry as geom
from memorymap_pipeline.roads import OVERPASS_ENDPOINTS, OVERPASS_TIMEOUT
import requests

route = load_route_from_gpx("Sample_Run.gpx")
all_lats = [p.latitude for p in route.points]
all_lons = [p.longitude for p in route.points]
center_lat = (min(all_lats) + max(all_lats)) / 2.0
center_lon = (min(all_lons) + max(all_lons)) / 2.0

print(f"Center: {center_lat}, {center_lon}")
print(f"Querying 200m radius around center...")

# Try Overpass API directly
endpoint = "https://overpass-api.de/api/interpreter"
query = f"""[out:json][timeout:30];
(
  way["highway"](around:200,{center_lat},{center_lon});
);
out body geom;
"""

try:
    resp = requests.post(endpoint, data={"data": query}, headers={"User-Agent": "test/1.0"}, timeout=60)
    data = resp.json()
    elements = data.get("elements", [])
    print(f"Found {len(elements)} ways from Overpass")
    
    # Count by type
    types = {}
    for el in elements:
        hw = el.get("tags", {}).get("highway", "unknown")
        types[hw] = types.get(hw, 0) + 1
    print(f"Highway types: {types}")
    
except Exception as e:
    print(f"Error querying Overpass: {e}")
