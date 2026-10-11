"""Capture slow exact buffers from a trusted local typification checkpoint."""

import copy
import json
import pickle
import subprocess
import sys
import time
from pathlib import Path

from shapely.geometry.base import BaseGeometry

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memorymap_pipeline import city_relief

root = Path("benchmarks/outputs/city-relief/performance")
inputs, kwargs = pickle.loads((root / "neighborhood.pickle").read_bytes())
source = subprocess.check_output(
    ["git", "show", "521c6e7:memorymap_pipeline/city_relief.py"], text=True
)
exec(compile(source, "city_reference.py", "exec"), city_relief.__dict__)  # noqa: S102
original = BaseGeometry.buffer
slow = []
counts = {"calls": 0, "seconds": 0.0}


def measured(geometry, *args, **kwargs):
    started = time.perf_counter()
    result = original(geometry, *args, **kwargs)
    elapsed = time.perf_counter() - started
    counts["calls"] += 1
    counts["seconds"] += elapsed
    if elapsed >= 0.05:
        slow.append((geometry, args, kwargs, elapsed))
    return result


BaseGeometry.buffer = measured
started = time.perf_counter()
try:
    result = city_relief.typify_neighborhoods(*copy.deepcopy(inputs), **kwargs)
finally:
    BaseGeometry.buffer = original
elapsed = time.perf_counter() - started
(root / "slow-occupied-buffers.pickle").write_bytes(pickle.dumps(slow))
report = {
    "reference_commit": "521c6e7",
    "elapsed_seconds": elapsed,
    "buffers": counts,
    "slow_calls": len(slow),
    "slow_seconds": sum(row[3] for row in slow),
    "cases": [
        {
            "seconds": row[3],
            "type": row[0].geom_type,
            "components": len(row[0].geoms) if hasattr(row[0], "geoms") else 1,
            "wkb_bytes": len(row[0].wkb),
            "radius": row[1][0] if row[1] else row[2].get("distance"),
        }
        for row in slow
    ],
}
(root / "slow-occupied-buffers.json").write_text(json.dumps(report, indent=2))
print(json.dumps({key: value for key, value in report.items() if key != "cases"}), flush=True)
