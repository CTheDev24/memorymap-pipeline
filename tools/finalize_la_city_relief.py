"""Finish authorized LA acceptance jobs serially after the active baseline exits."""

import argparse
import ctypes
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--baseline-pid", type=int, required=True)
args = parser.parse_args()
root = Path("benchmarks/outputs/city-relief").resolve()
state_path = root / "full-validation-state.json"
started = time.perf_counter()
state = {"passed": False, "release_accepted": False, "physical_prints": "pending"}


def record(phase, **extra):
    state.update(phase=phase, elapsed_seconds=time.perf_counter() - started, **extra)
    state_path.write_text(json.dumps(state, indent=2))
    print(phase, flush=True)


def model_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    with path.with_suffix(".audit.json").open(encoding="utf-8") as stream:
        header = stream.read(32768)
    match = re.search(r'"model_sha256"\s*:\s*"([a-f0-9]{64})"', header)
    assert match and match[1] == digest.hexdigest(), f"Audit hash mismatch: {path}"
    return digest.hexdigest()


try:
    record("waiting_for_existing_baseline")
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, args.baseline_pid)
    if handle:
        while kernel.WaitForSingleObject(handle, 30000) == 258:
            pass
        kernel.CloseHandle(handle)
    baseline = json.loads((root / "existing/metrics.json").read_text())
    assert baseline["statistics"]["layer_states"]["buildings"] == "complete"
    baseline_hash = model_hash(root / "existing/la.3mf")
    subprocess.run(
        [
            sys.executable,
            "tools/verify_city_relief_package.py",
            "dist/city-relief-reviewed/Trace Studio.exe",
            str(root / "package-provenance.json"),
        ],
        check=True,
        creationflags=0x08000000,
    )
    record("running_packaged_full_city_relief", baseline_sha256=baseline_hash)
    subprocess.run(
        ["powershell", "-NoProfile", "-File", "tools/validate_city_relief_package.ps1"],
        check=True,
        creationflags=0x08000000,
    )
    city = json.loads((root / "packaged-full-report.json").read_text())
    performance = json.loads(
        (root / "packaged-full-report.performance.json").read_text(encoding="utf-8-sig")
    )
    assert city["passed"] and city["statistics"]["layer_states"]["buildings"] == "complete"
    missing = [
        layer
        for layer in ("buildings", "roads", "route")
        if city["statistics"]["layer_states"].get(layer) != "complete"
    ]
    assert not missing, f"Required full LA layers did not complete: {missing}"
    # Existing baseline predates the City-only ancillary park-member repair.
    # Keep both actual identities; do not rewrite an already verified audit.
    baseline_build = baseline["statistics"]["build"]["source_sha256"]
    city_build = city["statistics"]["build"]["source_sha256"]
    if baseline_build != city_build:
        verification = json.loads((root / "package-output-verification.json").read_text())
        assert all(
            row["existing_mode_compatible"] and row["current_build_verified"]
            for row in verification
        )
    city_hash = model_hash(root / "packaged-full/la.3mf")
    comparisons = {}
    baseline_stages = baseline["statistics"]["building_generalization"]["stage_seconds"]
    city_stages = city["statistics"]["building_generalization"]["stage_seconds"]
    for name, duration in baseline_stages.items():
        value = city_stages.get(name)
        if value is not None and duration > 0:
            comparisons[name] = {
                "existing_seconds": duration,
                "city_seconds": value,
                "ratio": value / duration,
            }
    over_budget = [name for name, value in comparisons.items() if value["ratio"] > 2]
    peak_ratio = performance["peak_process_working_set_bytes"] / baseline["peak_rss_bytes"]
    report = {
        "baseline": baseline,
        "baseline_source_sha256": baseline_build,
        "city_source_sha256": city_build,
        "city": city,
        "packaged_performance": performance,
        "stage_comparisons": comparisons,
        "stages_over_twice_baseline": over_budget,
        "total_runtime_ratio": city["elapsed_seconds"] / baseline["elapsed_seconds"],
        "peak_memory_ratio": peak_ratio,
        "peak_memory_over_twice_baseline": peak_ratio > 2,
        "method": "Serial frozen-road/native-building-cache runs. Baseline is source Python; City uses verified one-file executable. Stage comparisons use audited generation timings. Baseline elapsed/peak excludes GLB export; packaged elapsed/peak includes it, and package wall time includes extraction. Total and peak ratios therefore remain diagnostic. Early baseline overlapped coupon validation; a controlled release benchmark remains pending.",
    }
    (root / "full-performance-comparison.json").write_text(json.dumps(report, indent=2))
    record("rendering_full_maps", city_sha256=city_hash, stages_over_twice_baseline=over_budget)
    subprocess.run(
        [sys.executable, "tools/render_exported_la_baseline.py"],
        check=True,
        creationflags=0x08000000,
    )
    for folder in ("existing", "packaged-full"):
        subprocess.run(
            [
                sys.executable,
                "tools/render_city_relief_preview.py",
                str(root / folder / ("la-exported.glb" if folder == "existing" else "la.glb")),
                "240",
                "190",
            ],
            check=True,
            creationflags=0x08000000,
        )
    record(
        "full_exports_and_renders_complete",
        passed=True,
        performance_acceptance="investigation_required"
        if over_budget or peak_ratio > 2
        else "controlled_benchmark_pending",
        physical_prints="pending",
        release_accepted=False,
    )
    for script in (
        "tools/summarize_la_building_losses.py",
        "tools/review_city_relief.py",
        "tools/package_city_relief_review.py",
    ):
        subprocess.run([sys.executable, script], check=True, creationflags=0x08000000)
except Exception as error:
    record("failed", passed=False, error=f"{type(error).__name__}: {error}")
    raise
