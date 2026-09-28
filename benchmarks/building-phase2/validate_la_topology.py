import json
import logging
import os
from pathlib import Path
from time import perf_counter
from memorymap_pipeline.buildings import _overpass_geometry

logging.basicConfig(level=logging.WARNING)
started = perf_counter()
seen = set()
unresolved = []
invalid = []
relations = 0
for path in sorted((Path(os.environ["LOCALAPPDATA"]) / "Trace Studio/building-cache-v1").glob("*.json")):
    for element in json.loads(path.read_text())["elements"]:
        key = (element.get("type"), element.get("id"))
        if key in seen:
            continue
        seen.add(key)
        geometry = _overpass_geometry(element)
        relations += key[0] == "relation"
        if geometry is None or geometry.is_empty:
            unresolved.append(key)
        elif not geometry.is_valid:
            invalid.append(key)
    print(f"Checked {len(seen)} unique records", flush=True)
report = dict(records=len(seen), relations=relations, unresolved=unresolved,
              invalid=invalid, elapsed_seconds=perf_counter()-started)
out = Path("benchmarks/outputs/la-topology")
out.mkdir(parents=True, exist_ok=True)
(out / "replay.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
assert not invalid
