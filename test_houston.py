from memorymap_pipeline.gpx_loader import load_route_from_gpx

route = load_route_from_gpx("Sample_Run.gpx")
lat = route.points[0].latitude
lon = route.points[0].longitude

print(f"Route: Sample_Run.gpx")
print(f"Coordinates: {lat}N, {lon}E")
print(f"Total points: {len(route.points)}")
print()

# Verify location
if 29.7 < lat < 29.8 and -95.4 < lon < -95.3:
    print("[OK] HOUSTON, TX, USA")
    print(f"Google Maps: https://www.google.com/maps/?q={lat},{lon}")
elif 51.4 < lat < 51.6 and -0.3 < lon < -0.0:
    print("Location: LONDON, UK")
else:
    print(f"Location: {lat}N, {lon}E")

# Check if closed loop
is_closed = (route.points[0].latitude == route.points[-1].latitude and 
             route.points[0].longitude == route.points[-1].longitude)
print(f"Closed loop: {is_closed}")
