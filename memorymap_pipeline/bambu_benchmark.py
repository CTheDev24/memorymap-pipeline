"""Save local Bambu toolpaths for benchmark specimens; never starts a print."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from .footprint_benchmark import _inside, _read, _sha, _write


def resolve_preset(profiles: Path, category: str, name: str, chain: tuple = ()) -> dict:
    """Resolve Bambu's installed inheritance/includes before CLI loading."""
    if name in chain:
        raise ValueError(f"Preset inheritance cycle: {name}")
    data = _read(_inside(profiles / category, name + ".json"))
    merged = {}
    if data.get("inherits"):
        merged.update(resolve_preset(profiles, category, data["inherits"], (*chain, name)))
    for included in data.get("include", []):
        merged.update(resolve_preset(profiles, category, included, (*chain, name)))
    merged.update(data)
    merged.pop("inherits", None)
    merged.pop("include", None)
    return merged


def toolpath_summary(project: Path) -> dict:
    """Layer extents are diagnostics only: they do not prove per-building survival."""
    plates = []
    with zipfile.ZipFile(project) as archive:
        for name in archive.namelist():
            if not name.endswith(".gcode"):
                continue
            text = archive.read(name).decode("utf-8", errors="replace")
            heights = [
                float(value) for value in re.findall(r"^; Z_HEIGHT: ([\d.]+)", text, re.MULTILINE)
            ]
            plates.append(
                {
                    "entry": name,
                    "layer_count": len(heights),
                    "last_layer_z_mm": max(heights) if heights else None,
                }
            )
    return {"plates": plates}


def slice_run(
    run_dir: Path,
    output: Path,
    executable: Path,
    profiles: Path,
    machine: str,
    process: str,
    filament: str,
) -> Path:
    report = _read(run_dir / "report.json")
    resolved = {
        role: resolve_preset(profiles, role, name)
        for role, name in (("machine", machine), ("process", process), ("filament", filament))
    }
    nozzle = resolved["machine"]["nozzle_diameter"]
    nozzle = nozzle[0] if isinstance(nozzle, list) else nozzle
    layer = resolved["process"]["layer_height"]
    layer = layer[0] if isinstance(layer, list) else layer
    if (
        float(nozzle) != report["profile"]["nozzle_diameter_mm"]
        or float(layer) != report["profile"]["layer_height_mm"]
    ):
        raise ValueError("Selected nozzle/layer profile differs from benchmark")
    output = output.resolve()
    executable = executable.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=False)
    for role, preset in resolved.items():
        _write(output / f"{role}.json", preset)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    help_result = subprocess.run(
        [str(executable), "--help"],
        cwd=output,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        creationflags=flags,
    )
    version = re.search(r"BambuStudio-([^\s:]+)", help_result.stdout)
    entries = []
    result_manifest = {
        "report_sha256": _sha(run_dir / "report.json"),
        "slicer_version": version.group(1) if version else "unknown",
        "machine": machine,
        "process": process,
        "filament": filament,
        "executable_sha256": _sha(executable),
        "presets_sha256": {role: _sha(output / f"{role}.json") for role in resolved},
        "specimens": entries,
        "acceptance": "manual toolpath review and physical print pending",
    }
    for specimen in report["specimens"]:
        name = specimen["name"]
        model = _inside(run_dir, name + ".3mf")
        if specimen.get("export_error") or not model.exists():
            entries.append({"specimen": name, "status": "blocked_by_geometry"})
            _write(output / "slicing.json", result_manifest)
            continue
        if _sha(model) != specimen["model_sha256"]:
            raise ValueError(f"Model checksum mismatch: {name}")
        target = _inside(output, name)
        target.mkdir()
        command = [
            str(executable),
            "--load-settings",
            str(output / "machine.json") + ";" + str(output / "process.json"),
            "--load-filaments",
            str(output / "filament.json"),
            "--slice",
            "0",
            "--arrange",
            "1",
            "--ensure-on-bed",
            "--export-3mf",
            "sliced.3mf",
            "--export-settings",
            "settings.json",
            "--outputdir",
            str(target),
            str(model),
        ]
        _write(target / "command.json", command)
        with (target / "bambu.log").open("w", encoding="utf-8") as log:
            try:
                result = subprocess.run(
                    command,
                    cwd=target,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=180,
                    creationflags=flags,
                )
                exit_code = result.returncode
            except subprocess.TimeoutExpired:
                exit_code = "timeout"
        project = target / "sliced.3mf"
        summary = (
            toolpath_summary(project) if exit_code == 0 and project.is_file() else {"plates": []}
        )
        entry = {
            "specimen": name,
            "exit_code": exit_code,
            "status": "toolpaths_generated" if summary["plates"] else "slicing_failed",
            "source_model_sha256": _sha(model),
            **summary,
        }
        if summary["plates"]:
            entry.update(project=str(project.relative_to(output)), project_sha256=_sha(project))
        entries.append(entry)
        print(name, entry["status"], summary, flush=True)
        _write(output / "slicing.json", result_manifest)
    if not entries:
        raise ValueError("Benchmark has no specimens")
    return output / "slicing.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--process", required=True)
    parser.add_argument("--filament", required=True)
    args = parser.parse_args()
    print(
        slice_run(
            args.run,
            args.output,
            args.executable,
            args.profiles,
            args.machine,
            args.process,
            args.filament,
        )
    )


if __name__ == "__main__":
    main()
