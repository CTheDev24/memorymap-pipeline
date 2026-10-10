"""Frozen LA replay with process metrics; no network allowed unless explicitly selected."""

import argparse
import ctypes
import json
import logging
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--mode", default="existing", choices=["existing", "balanced", "dense"])
parser.add_argument("--source-root")
parser.add_argument("--network", action="store_true")
parser.add_argument("--roads-file")
parser.add_argument("--line-width", type=float)
args = parser.parse_args()
if args.source_root:
    sys.path.insert(0, args.source_root)
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
from memorymap_pipeline.gpx_loader import load_route_from_gpx
from memorymap_pipeline.map_frame import MapFrame

if not args.network:
    import requests

    def offline(*a, **k):
        raise requests.ConnectionError("Offline replay: uncached network request refused")

    requests.sessions.Session.request = offline
logging.basicConfig(level=logging.WARNING)
audit = json.loads(Path(r"C:/Users/craig/Downloads/la_test.audit.json").read_text())
settings = audit["settings"]
if args.line_width:
    settings["line_width_mm"] = args.line_width
if args.mode != "existing":
    settings.update(building_appearance_mode="city_relief", city_relief_detail=args.mode)
out = Path("benchmarks/outputs/city-relief") / args.mode
out.mkdir(parents=True, exist_ok=True)
peak = [0]
stop = threading.Event()


class MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (n, ctypes.c_size_t)
        for n in [
            "PeakWorkingSetSize",
            "WorkingSetSize",
            "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage",
            "PagefileUsage",
            "PeakPagefileUsage",
        ]
    ]


ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
process = ctypes.windll.kernel32.GetCurrentProcess()
ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
]


def memory():
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
    return counters.PeakWorkingSetSize


def sample():
    while not stop.wait(0.5):
        peak[0] = max(peak[0], memory())


threading.Thread(target=sample, daemon=True).start()
t0 = time.perf_counter()
try:
    result = generate_memory_map(
        GenerationRequest(
            route=load_route_from_gpx(
                r"C:/Users/craig/iCloudDrive/Business_/Teklo Studio/Product Development/Trace/GPX/LA Marathon.gpx"
            ),
            frame=MapFrame(**audit["frame"]),
            config=settings,
            roads_file=args.roads_file,
            output_path=out / "la.3mf",
        ),
        progress_callback=lambda p, t: print(
            f"{time.perf_counter() - t0:.1f}s {p}% {t}", flush=True
        ),
    )
    (out / "metrics.json").write_text(
        json.dumps(
            {
                "elapsed_seconds": time.perf_counter() - t0,
                "peak_rss_bytes": peak[0],
                "statistics": result.stats,
                "warnings": result.warnings,
            },
            indent=2,
        )
    )
    from memorymap_pipeline.desktop.preview import export_preview_glb

    export_preview_glb(result.meshes, out / "la.glb")
    print("COMPLETE", result.output_path, flush=True)
finally:
    stop.set()
