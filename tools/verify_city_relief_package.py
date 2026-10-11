"""Verify that a review executable contains the current algorithms and assets."""

import argparse
import hashlib
import importlib.util
import json
import marshal
import sys
from pathlib import Path
from types import CodeType

from PyInstaller.archive.readers import CArchiveReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
parser = argparse.ArgumentParser()
parser.add_argument("executable", type=Path)
parser.add_argument("report", type=Path)
args = parser.parse_args()
archive = CArchiveReader(str(args.executable))
pyz = archive.open_embedded_archive(next(name for name in archive.toc if name.startswith("PYZ")))


def normalized(code):
    return code.replace(
        co_filename="<source>",
        co_consts=tuple(normalized(x) if isinstance(x, CodeType) else x for x in code.co_consts),
    )


modules = {}
for name in [
    "build_identity",
    "buildings",
    "building_download",
    "overpass_geometry",
    "building_optimization",
    "building_grouping",
    "spatial_area",
    "building_generalization",
    "building_heights",
    "city_relief",
    "generation",
    "map_styles",
    "street_selection",
    "roads",
    "mesh",
    "printability",
    "config",
    "desktop.__init__",
    "desktop.project",
    "desktop.window",
    "desktop.worker",
    "desktop.validation",
    "desktop.smoke_test",
]:
    full = "memorymap_pipeline." + name
    if name == "desktop.__init__":
        full = "memorymap_pipeline.desktop"
    packed = normalized(pyz.extract(full))
    source = normalized(importlib.util.find_spec(full).loader.get_code(full))
    modules[full] = {
        "matches_source": packed == source,
        "packaged_sha256": hashlib.sha256(marshal.dumps(packed)).hexdigest(),
        "source_sha256": hashlib.sha256(marshal.dumps(source)).hexdigest(),
    }
assets = {}
for asset in ["data/landmarks.v1.json", "desktop/viewer/index.html"]:
    key = next(k for k in archive.toc if k.replace("\\", "/") == "memorymap_pipeline/" + asset)
    packed = archive.extract(key)
    source = (Path("memorymap_pipeline") / asset).read_bytes()
    assets[asset] = {
        "matches_source": packed == source,
        "sha256": hashlib.sha256(packed).hexdigest(),
    }
report = {
    "passed": all(x["matches_source"] for x in [*modules.values(), *assets.values()]),
    "executable": str(args.executable.resolve()),
    "executable_sha256": hashlib.file_digest(args.executable.open("rb"), "sha256").hexdigest(),
    "modules": modules,
    "assets": assets,
}
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(report, indent=2))
print("Package matches source:", report["passed"])
sys.exit(0 if report["passed"] else 1)
