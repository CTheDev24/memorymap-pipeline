"""Collect candidate geometry, reviews, and acceptance evidence without source caches."""

import zipfile
from pathlib import Path

root = Path("benchmarks/outputs/city-relief")
target = root / "city-relief-review.zip"
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    archive.write("docs/city-relief.md", "README.md")
    for name in (
        "review.html",
        "prototype-summary.json",
        "package-provenance.json",
        "package-startup-smoke.json",
        "package-output-verification.json",
        "tests-final.log",
        "full-validation-state.json",
        "full-performance-comparison.json",
    ):
        path = root / name
        if path.exists():
            archive.write(path, name)
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
            "top.png",
            "perspective.png",
            "side.png",
        ):
            path = root / folder / name
            if path.exists():
                archive.write(path, path.relative_to(root))
print(target, target.stat().st_size)
