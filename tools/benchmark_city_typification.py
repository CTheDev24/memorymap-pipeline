"""Capture trusted local frozen inputs, then replay typification without export.

Pickles are local developer checkpoints: never load an untrusted checkpoint.
"""

import argparse
import copy
import cProfile
import hashlib
import json
import pickle
import runpy
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memorymap_pipeline import city_relief

parser = argparse.ArgumentParser()
parser.add_argument("area", choices=["downtown", "neighborhood", "landmark"])
parser.add_argument("--capture", action="store_true")
parser.add_argument("--label", default="baseline")
parser.add_argument("--repeats", type=int, default=3)
parser.add_argument(
    "--reference-commit", help="Replay the City module from a local trusted Git revision"
)
parser.add_argument(
    "--max-candidates",
    type=int,
    default=1000,
    help="Stop at a deterministic seed checkpoint; 0 runs all",
)
args = parser.parse_args()
root = Path("benchmarks/outputs/city-relief/performance")
root.mkdir(parents=True, exist_ok=True)
checkpoint = root / f"{args.area}.pickle"
if args.capture:

    def capture(*inputs, **kwargs):
        kwargs.pop("progress_callback", None)
        with checkpoint.open("wb") as stream:
            pickle.dump((inputs, kwargs), stream)
        print(f"Captured {checkpoint}", flush=True)
        raise SystemExit(0)

    city_relief.typify_neighborhoods = capture
    sys.argv = [
        "compare_la_city_relief.py",
        "--area",
        args.area,
        "--mode",
        "balanced",
        "--line-width",
        "0.42",
    ]
    runpy.run_path("tools/compare_la_city_relief.py", run_name="__main__")
inputs, kwargs = pickle.loads(checkpoint.read_bytes())
if args.reference_commit:
    source = subprocess.check_output(
        ["git", "show", f"{args.reference_commit}:memorymap_pipeline/city_relief.py"], text=True
    )
    # Execute only the explicitly selected local trusted Git module, never a checkpoint payload.
    exec(compile(source, "city_relief_reference.py", "exec"), city_relief.__dict__)  # noqa: S102
times = []
digests = []


class BudgetReached(Exception):
    pass


def replay(local):
    def progress(fraction, message):
        position = int(message.rsplit(" ", 1)[1].split("/")[0])
        if args.max_candidates and position >= args.max_candidates:
            raise BudgetReached()

    try:
        return city_relief.typify_neighborhoods(*local, **kwargs, progress_callback=progress)
    except BudgetReached:
        return local[0]


for repeat in range(args.repeats):
    local = copy.deepcopy(inputs)
    started = time.perf_counter()
    result = replay(local)
    times.append(time.perf_counter() - started)
    # Includes dispositions, provenance, geometry, and heights in stable ordering.
    payload = pickle.dumps(
        (
            result.decisions,
            sorted(result.omitted),
            [p.wkb for p in result.footprints],
            [(g.members, g.geometry.wkb) for g in result.groups],
            result.group_heights,
        )
    )
    digests.append(hashlib.sha256(payload).hexdigest())
    print(args.area, args.label, repeat, times[-1], digests[-1], flush=True)
profile = cProfile.Profile()
profile.runcall(replay, copy.deepcopy(inputs))
profile.dump_stats(str(root / f"{args.area}-{args.label}.prof"))
import ctypes
from ctypes import wintypes


class MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
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
ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(MemoryCounters),
    wintypes.DWORD,
]
if not ctypes.windll.psapi.GetProcessMemoryInfo(
    ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
):
    raise ctypes.WinError()
report = {
    "area": args.area,
    "label": args.label,
    "seconds": times,
    "output_sha256": digests,
    "reference_commit": args.reference_commit,
    "candidate_budget": args.max_candidates,
    "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
    "peak_working_set_bytes": counters.PeakWorkingSetSize,
    "measurement": "Process OS peak includes checkpoint loading, replay and separately profiled replay; timings exclude deepcopy and profiling.",
}
(root / f"{args.area}-{args.label}.json").write_text(json.dumps(report, indent=2))
