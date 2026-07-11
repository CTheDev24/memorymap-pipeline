import requests
import sys
from datetime import datetime

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Simple test query to check if server responds
TEST_QUERY = """[out:json][timeout:10];
(
  node["name"="test"](0,0,1,1);
);
out;
"""

print(f"\n{'='*70}")
print(f"OSM Overpass API Status Check - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*70}\n")

results = []

for endpoint in ENDPOINTS:
    endpoint_name = endpoint.split('//')[1].split('/')[0]
    print(f"Testing: {endpoint_name}")
    print(f"  URL: {endpoint}")
    
    try:
        response = requests.post(
            endpoint,
            data={"data": TEST_QUERY},
            headers={"User-Agent": "osm-status-checker/1.0"},
            timeout=15
        )
        
        status_code = response.status_code
        status_text = "[OK]" if status_code == 200 else f"[HTTP {status_code}]"
        
        print(f"  Status: {status_text}")
        
        if status_code == 200:
            try:
                data = response.json()
                print(f"  Response: Valid JSON")
                results.append((endpoint_name, "[OK] Working"))
            except:
                print(f"  Response: Invalid JSON")
                results.append((endpoint_name, "[WARN] Unclear"))
        else:
            print(f"  Response: {response.reason}")
            results.append((endpoint_name, f"[FAIL] HTTP {status_code}"))
            
    except requests.exceptions.Timeout:
        print(f"  Status: [TIMEOUT]")
        results.append((endpoint_name, "[FAIL] Timeout"))
    except requests.exceptions.ConnectionError as e:
        print(f"  Status: [CONNECTION ERROR]")
        results.append((endpoint_name, "[FAIL] Connection Error"))
    except Exception as e:
        print(f"  Status: [ERROR] {type(e).__name__}")
        results.append((endpoint_name, f"[FAIL] {type(e).__name__}"))
    
    print()

print(f"{'='*70}")
print("SUMMARY")
print(f"{'='*70}")

working = sum(1 for _, status in results if "[OK]" in status)
print(f"\nWorking endpoints: {working}/{len(ENDPOINTS)}\n")

for name, status in results:
    print(f"  {status:25} {name}")

if working > 0:
    print(f"\n[OK] At least one endpoint is working! Try again.")
else:
    print(f"\n[FAIL] No endpoints working.")
    print(f"\nAlternatives:")
    print(f"  1. Wait and retry later")
    print(f"  2. Use local GeoJSON/GeoPackage files")
    print(f"  3. Check: https://status.openstreetmap.org/")

print(f"\n{'='*70}\n")
