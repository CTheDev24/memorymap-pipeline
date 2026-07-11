# Testing Buildings Generation

## Prerequisites
1. Check OSM API status first:
   ```bash
   python check_osm_status.py
   ```
   All 4 endpoints should show [OK] before proceeding.

## Test Buildings Only
```bash
python test_buildings_only.py
```
Output file: `test_buildings_only.3mf` (base plate + buildings only, no route/roads)

## Test Full Pipeline
```bash
python main.py Sample_Run.gpx houston_test.3mf
```
Output file: `houston_test.3mf` (base + route + roads + buildings)

## Current Issue
OSM Overpass API is rate-limited (HTTP 406, 403 errors). This is temporary.

**Workaround:** Use local GeoJSON/GeoPackage files:
```bash
python main.py Sample_Run.gpx output.3mf \
  --buildings-file path/to/buildings.geojson \
  --roads-file path/to/roads.geojson
```

## What Changed
- Expanded road types from 6 to 14 (now includes links, service, unclassified, living_street)
- Fixed projection to use geographic center instead of first point
- Buildings should now span full map when OSM is available

## Expected Results
- Route: 224×162mm (spans full map minus margins)
- Roads: 100-150mm span (varies by area density)
- Buildings: 220×170mm (spans full map minus margins)
