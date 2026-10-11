"""Replay trusted local frozen road regions; never load an untrusted pickle."""

import argparse
import ctypes
import json
import pickle
import subprocess
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memorymap_pipeline import roads

parser = argparse.ArgumentParser()
parser.add_argument("--label", required=True)
parser.add_argument("--reference-commit")
args = parser.parse_args()
root = Path("benchmarks/outputs/city-relief/road-diagnostic")
regions = pickle.loads((root / "regions.pickle").read_bytes())["regions"]
module = roads
if args.reference_commit:
    module = types.ModuleType("memorymap_pipeline.road_reference")
    module.__package__ = "memorymap_pipeline"
    source = subprocess.check_output(
        ["git", "show", f"{args.reference_commit}:memorymap_pipeline/roads.py"], text=True
    )
    exec(compile(source, "road_reference.py", "exec"), module.__dict__)  # noqa: S102
stats = {}
started = time.perf_counter()
meshes = module._extrude_road_regions(
    regions, hierarchical=True, embed_depth_mm=0.2, z_offset=1.4, processing_stats=stats
)
elapsed = time.perf_counter() - started
area = 0.0
for mesh in meshes:
    triangles = mesh.triangles[:, :, :2]
    first = triangles[:, 1] - triangles[:, 0]
    second = triangles[:, 2] - triangles[:, 0]
    cross = first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]
    area += cross[cross > 0].sum() * 0.5
expected = sum(
    polygon.area
    for region, _ in regions
    for polygon in roads._polygon_parts(region)
    if polygon.area > 1e-8
)


class MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
        (name, ctypes.c_size_t)
        for name in (
            "PeakWorkingSetSize",
            "WorkingSetSize",
            "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage",
            "PagefileUsage",
            "PeakPagefileUsage",
        )
    ]


counters = MemoryCounters()
counters.cb = ctypes.sizeof(counters)
ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_ulong,
]
if not ctypes.windll.psapi.GetProcessMemoryInfo(
    ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
):
    raise ctypes.WinError()
report = {
    "label": args.label,
    "reference_commit": args.reference_commit,
    "seconds": elapsed,
    "mesh_parts": len(meshes),
    "faces": sum(len(mesh.faces) for mesh in meshes),
    "all_closed": all(mesh.is_watertight for mesh in meshes),
    "all_consistently_wound": all(mesh.is_winding_consistent for mesh in meshes),
    "eligible_polygon_area_mm2": expected,
    "mesh_top_projected_area_mm2": float(area),
    "net_area_difference_mm2": float(area - expected),
    "processing": stats,
    "peak_process_working_set_bytes": counters.PeakWorkingSetSize,
    "scope": "Cached exact regions only; excludes acquisition, buffering and export",
}
report["passed"] = (
    not stats.get("failed_polygon_count") and not stats.get("processing_error")
    and report["all_closed"] and report["all_consistently_wound"]
    and abs(report["net_area_difference_mm2"]) <= max(1e-6, expected * 1e-9)
)
(root / f"{args.label}.json").write_text(json.dumps(report, indent=2))
print(json.dumps({key: value for key, value in report.items() if key != "processing"}), flush=True)
