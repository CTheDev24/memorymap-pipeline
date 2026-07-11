from memorymap_pipeline.gpx_loader import load_route_from_gpx
import math

route = load_route_from_gpx("Evening_run.gpx")
lat = route.points[0].latitude
lon = route.points[0].longitude

print(f"Route coordinates: {lat}°N, {lon}°E")
print()

# Identify location
if 51.4 < lat < 51.6 and -0.3 < lon < -0.0:
    print("Location: LONDON, UK (Westminster area)")
elif 29.7 < lat < 29.8 and -95.4 < lon < -95.3:
    print("Location: HOUSTON, TX, USA")
else:
    print(f"Location: Unknown - {lat}°N, {lon}°E")

# Show on map
print(f"\nGoogle Maps: https://www.google.com/maps/?q={lat},{lon}")
