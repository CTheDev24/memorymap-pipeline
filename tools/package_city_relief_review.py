"""Collect candidate geometry, reviews, and acceptance evidence without source caches."""

import zipfile
from pathlib import Path

root = Path("benchmarks/outputs/city-relief")
target = root / "city-relief-review.zip"
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    archive.write("docs/city-relief.md", "README.md")
    for name in (
        "city-relief-checkpoint.md",
        "city-relief-checkpoint-metrics.json",
        "city-relief-print-checklist.md",
        "city-relief-full-validation.md",
        "city-relief-full-validation-metrics.json",
    ):
        archive.write(Path("docs") / name, name)
    for name in (
        "review.html",
        "prototype-summary.json",
        "package-provenance.json",
        "package-startup-smoke.json",
        "package-output-verification.json",
        "tests-final.log",
        "full-validation-state.json",
        "full-performance-comparison.json",
        "building-loss-breakdown.json",
        "performance-investigation.json",
        "city-full-typification.speedscope.json",
        "optimized-full-checkpoint.json",
        "optimized-full-first-pass-checkpoint.json",
        "optimized-full-typification-complete-checkpoint.json",
        "optimized-performance-investigation.json",
        "city-full-optimized-typification.speedscope.json",
        "fallback-performance-investigation.json",
        "fallback-progress-observations.jsonl",
        "city-full-fallback.speedscope.json",
        "approved-input-snapshot.json",
        "serialized-export-verification.json",
        "packaged-full-report.json",
        "packaged-full-report.performance.json",
        "performance/tests-checkpoint.log",
        "tests-roadfix.log",
        "performance/envelope-cache-evaluation.json",
        "road-diagnostic/hierarchy.json",
        "road-diagnostic/fixed-regions.json",
        "roadfix-package-provenance.json",
        "roadfix-package-startup-smoke.json",
        "roadfix-full-roads-report.json",
        "roadfix-full-roads-report.performance.json",
        "roadfix-neighborhood-report.json",
        "roadfix-neighborhood-report.performance.json",
        "roadfix-offline-report.json",
        "roadfix-offline-report.performance.json",
        "roadfix-offline-verification.json",
        "physical-review-status.txt",
    ):
        path = root / name
        if path.exists():
            archive.write(path, name)
    for path in (root / "roadfix-offline-fixture").glob("*"):
        if path.is_file():
            archive.write(path, path.relative_to(root))
    for area in ("downtown", "neighborhood", "landmark"):
        for mode in ("existing", "balanced", "dense"):
            folder = root / "prototypes" / area / mode
            for name in (
                "sample.3mf",
                "sample.audit.json",
                "metrics.json",
                "top.png",
                "perspective.png",
                "side.png",
            ):
                path = folder / name
                archive.write(path, path.relative_to(root))
        for suffix in ("sliced.3mf", "source.3mf", "source.audit.json", "result.json"):
            path = root / "slicer" / f"{area}-final-{suffix}"
            archive.write(path, path.relative_to(root))
    for name in ("machine.json", "process.json", "filament.json", "toolpath-survival.json"):
        path = root / "slicer" / name
        archive.write(path, path.relative_to(root))
    for folder in ("existing", "packaged-full"):
        for name in (
            "la.3mf",
            "la.audit.json",
            "metrics.json",
            "preview-provenance.json",
            "top.png",
            "perspective.png",
            "side.png",
        ):
            path = root / folder / name
            if path.exists():
                archive.write(path, path.relative_to(root))
print(target, target.stat().st_size)
