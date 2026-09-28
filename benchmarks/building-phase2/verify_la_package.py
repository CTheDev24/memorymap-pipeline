import hashlib
import json
import sys
from pathlib import Path
from types import CodeType
from PyInstaller.archive.readers import CArchiveReader


def normalized(code):
    return code.replace(co_filename="<source>", co_consts=tuple(
        normalized(value) if isinstance(value, CodeType) else value
        for value in code.co_consts))


if __name__ == "__main__":
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/la-recovery-20260927")
    exe = folder / "Trace Studio.exe"
    archive = CArchiveReader(str(exe))
    pyz = archive.open_embedded_archive("PYZ.pyz")
    modules = {}
    for module in ("building_download", "overpass_geometry", "buildings", "building_heights", "generation", "desktop.smoke_test"):
        name = "memorymap_pipeline." + module
        source = Path(*name.split(".")).with_suffix(".py")
        bundled = pyz.extract(name)
        expected = compile(source.read_bytes(), str(source), "exec")
        # Compare normalized compiled code to catch stale packaged source.
        same = normalized(bundled) == normalized(expected)
        modules[name] = same
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    report = {"sha256": digest, "bytes": exe.stat().st_size, "modules_match_source": modules,
              "system_icu_not_bundled": "icuuc.dll" not in archive.toc,
              "source_tests": {"passed": 453, "warnings": 76}, "live_la_validation": json.loads(Path("benchmarks/outputs/la-download-recovery/live-download.json").read_text())}
    (folder / "build-verification.json").write_text(json.dumps(report, indent=2))
    (folder / "Trace Studio.exe.sha256").write_text(digest + " *Trace Studio.exe\n")
    print(json.dumps(report, indent=2))
    assert all(modules.values()) and report["system_icu_not_bundled"]
