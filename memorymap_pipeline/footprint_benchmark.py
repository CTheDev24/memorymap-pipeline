"""Offline, versioned building-footprint specimens; never a slicing certificate.

Run ``python -m memorymap_pipeline.footprint_benchmark --help``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import math
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime
from html import escape
from pathlib import Path

import numpy as np
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union
from trimesh import Trimesh
from trimesh.intersections import slice_mesh_plane
from trimesh.util import concatenate

from .config import DEFAULT_CONFIG
from .generation import GenerationRequest, generate_memory_map
from .gpx_loader import load_route_from_gpx
from .map_frame import MapFrame
from .mesh import build_base_plate, export_3mf, route_mesh_from_polygon
from .printability import audit_printability
from .terrain import ElevationGrid

PROFILE = {
    "nozzle_diameter_mm": 0.4,
    "layer_height_mm": 0.16,
    "screening_width_mm": 0.8,
    "base_thickness_mm": 1.6,
}
LAYERS = (
    "base",
    "route",
    "roads",
    "buildings",
    "water",
    "landscape",
    "start_marker",
    "finish_marker",
)


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", value):
        raise ValueError(f"Unsafe specimen name: {value!r}")
    return value


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Artifact paths must stay inside the benchmark directory")
    return path


def _bounds(values) -> tuple[float, float, float, float]:
    if len(values) != 4:
        raise ValueError("Crop bounds require xmin, ymin, xmax, ymax in print mm")
    x0, y0, x1, y1 = map(float, values)
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x0 >= x1 or y0 >= y1:
        raise ValueError("Crop bounds must be finite and have positive area")
    return x0, y0, x1, y1


def freeze(spec_path: Path, output: Path) -> Path:
    """Copy explicit local inputs, provenance and resolved settings into a new snapshot."""
    spec = _read(spec_path)
    paths = {}
    for role in ("route", "buildings", "roads", "water"):
        source = spec["sources"][role]
        for key in ("path", "origin", "acquired_at", "license"):
            if not source.get(key) or str(source[key]).startswith("REPLACE"):
                raise ValueError(f"{role} requires explicit {key}")
        paths[role] = (spec_path.parent / source["path"]).resolve(strict=True)
        if role != "route":
            import geopandas as gpd

            if paths[role].suffix.lower() != ".geojson":
                raise ValueError("Snapshot inputs must be GeoJSON files")
            data = gpd.read_file(paths[role])
            if data.crs is None or data.crs.to_epsg() != 4326:
                raise ValueError(f"{role} must be WGS84 / EPSG:4326 GeoJSON")
    route = load_route_from_gpx(paths["route"])
    fitted = MapFrame.fit_route(route.points, 190.0, 240.0, margin_mm=5.0, route_padding_mm=0.6)
    # Keep the fitted route scale while allowing map content to reach the edge.
    # MapFrame.margin_mm controls physical trim; route clearance is independent.
    frame = replace(
        fitted,
        margin_mm=0.0,
        coverage_width_m=fitted.coverage_width_m * 190.0 / fitted.printable_width_mm,
        coverage_height_m=fitted.coverage_height_m * 240.0 / fitted.printable_height_mm,
    )
    crops = []
    for crop in spec["crops"]:
        name = _name(crop["name"])
        if "bounds_mm" in crop:
            bounds = _bounds(crop["bounds_mm"])
        else:
            lat, lon = crop["center_latlon"]
            x, y = frame.transform_lonlat(np.array([lat]), np.array([lon]))[0]
            half = float(crop.get("size_mm", 25.0)) / 2
            bounds = _bounds([x - half, y - half, x + half, y + half])
        if not box(0, 0, 190, 240).covers(box(*bounds)):
            raise ValueError(f"Crop {name} lies outside the full-route plate")
        crops.append({"name": name, "bounds_mm": bounds})
    if not crops or len({c["name"] for c in crops}) != len(crops):
        raise ValueError("Provide at least one crop with unique names")
    config = deepcopy(DEFAULT_CONFIG)
    # Explicit flat terrain permits local water without any elevation service calls.
    config.update(
        flat_border_enabled=False,
        terrain_enabled=True,
        terrain_grid_size=32,
        water_enabled=True,
        style_profile="urban",
    )
    output.mkdir(parents=True, exist_ok=False)
    sources = {}
    for role, source_path in paths.items():
        target = output / (role + (".gpx" if role == "route" else ".geojson"))
        shutil.copyfile(source_path, target)
        sources[role] = {**spec["sources"][role], "path": target.name, "sha256": _sha(target)}
    manifest = {
        "schema_version": 1,
        "name": spec["name"],
        "created_at": datetime.now(UTC).isoformat(),
        "coverage_note": spec.get("coverage_note", "Coverage not independently verified"),
        "frame": asdict(frame),
        "route_clearance_mm": 5.0,
        "profile": PROFILE,
        "config": config,
        "sources": sources,
        "crops": crops,
        "terrain": "flat test control; not measured Chicago terrain",
    }
    _write(output / "manifest.json", manifest)
    return output / "manifest.json"


def screen_footprint(polygon, width_mm: float = 0.8) -> dict:
    """Morphological diagnostic, not proof that a slicer will preserve a feature.

    Mitred opening avoids treating the corners of a broad rectangle as thin details.
    It can reveal sub-threshold appendages and necks missed by global extents.
    """
    if not math.isfinite(width_mm) or width_mm <= 0:
        raise ValueError("Screening width must be finite and positive")
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        return {"status": "invalid", "core_empty": True, "lost_area_fraction": 1.0}
    # GEOS collapses almost-zero cores; allow 0.0001 mm numerical tolerance.
    radius = max(0.0, width_mm / 2 - 1e-4)
    core = polygon.buffer(-radius, join_style=2)
    opened = core.buffer(radius, join_style=2).intersection(polygon)
    loss = max(0.0, min(1.0, 1.0 - opened.area / polygon.area))
    return {
        "status": "at_risk" if core.is_empty or loss > 1e-5 else "not_flagged",
        "core_empty": core.is_empty,
        "lost_area_fraction": loss,
        "area_mm2": polygon.area,
        "screening_width_mm": width_mm,
    }


def crop_mesh(mesh: Trimesh, bounds) -> Trimesh | None:
    """Clip closed shells independently, cap cuts, and translate XY only."""
    x0, y0, x1, y1 = _bounds(bounds)
    parts = []
    for component in mesh.split(only_watertight=False):
        low, high = component.bounds
        if high[0] <= x0 or high[1] <= y0 or low[0] >= x1 or low[1] >= y1:
            continue
        if not (low[0] >= x0 and high[0] <= x1 and low[1] >= y0 and high[1] <= y1):
            for normal, origin in [
                ([1, 0, 0], [x0, 0, 0]),
                ([-1, 0, 0], [x1, 0, 0]),
                ([0, 1, 0], [0, y0, 0]),
                ([0, -1, 0], [0, y1, 0]),
            ]:
                component = slice_mesh_plane(component, normal, origin, cap=True, engine="earcut")
                # Plane intersections can collapse a triangle to an edge. Such
                # zero-area faces add no surface but break edge-incidence checks.
                # Do not use a positive size threshold: tiny real faces matter.
                if not component.is_watertight:
                    component.update_faces(component.nondegenerate_faces(height=0.0))
                    component.remove_unreferenced_vertices()
                if not len(component.faces):
                    break
        if len(component.faces):
            if not component.is_watertight:
                raise ValueError("Crop produced an open shell; specimen cannot be exported")
            component.apply_translation((-x0, -y0, 0))
            parts.append(component)
    return concatenate(parts) if parts else None


def _export(path: Path, meshes: dict) -> None:
    export_3mf(
        path,
        meshes.get("base"),
        meshes.get("route"),
        **{f"{name}_mesh": meshes.get(name) for name in LAYERS[2:]},
    )


def _export_face_delta(path: Path, mesh: Trimesh | None) -> int:
    """Expose exporter cleanup instead of counting deleted shells as retained."""
    count = 0
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith(".model"):
                root = ET.fromstring(archive.read(name))
                for obj in root.findall(".//{*}object"):
                    if obj.get("name") == "Buildings_Verification":
                        count += len(obj.findall("./{*}mesh/{*}triangles/{*}triangle"))
    return (len(mesh.faces) if mesh is not None else 0) - count


def _attempt_export(path: Path, meshes: dict) -> dict:
    try:
        _export(path, meshes)
    except ValueError as exc:
        return {
            "export_error": str(exc),
            "model_sha256": None,
            "export_removed_building_faces": None,
        }
    return {
        "export_error": None,
        "model_sha256": _sha(path),
        "export_removed_building_faces": _export_face_delta(path, meshes.get("buildings")),
    }


def _overlay(path: Path, bounds, records: list[dict], context_meshes: dict | None = None) -> None:
    x0, y0, x1, y1 = bounds
    w, h = x1 - x0, y1 - y0
    pieces = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" '
            f'viewBox="0 0 {w} {h}"><rect width="100%" height="100%" fill="white"/>'
        )
    ]
    for layer, color in (("water", "#b6e4ef"), ("roads", "#777777"), ("route", "#ff9933")):
        mesh = (context_meshes or {}).get(layer)
        if mesh is None:
            continue
        triangles = mesh.triangles
        upward = (
            np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 2]
            > 1e-9
        )
        for triangle in triangles[upward]:
            points = " ".join(f"{x:.5f},{h - y:.5f}" for x, y, _z in triangle)
            pieces.append(f'<polygon points="{points}" fill="{color}"/>')
    group_numbers = {}
    for number, record in enumerate(records, 1):
        group_id = record.get("group_id")
        if group_id in group_numbers:
            record["overlay_number"] = group_numbers[group_id]
            continue
        if group_id:
            group_numbers[group_id] = number
        geometry = record.get("output_print_geometry") or record.get("source_print_geometry")
        if geometry is None:
            continue
        polygon = shape(geometry).intersection(box(*bounds))
        for part in getattr(polygon, "geoms", [polygon]):
            if part.is_empty or part.geom_type != "Polygon":
                continue
            rings = [part.exterior, *part.interiors]
            commands = []
            for ring in rings:
                commands.append(
                    "M " + " L ".join(f"{x - x0:.5f},{y1 - y:.5f}" for x, y in ring.coords) + " Z"
                )
            color = (
                "#d94d39" if record.get("screening", {}).get("status") == "at_risk" else "#7d91a6"
            )
            pieces.append(
                f'<path d="{" ".join(commands)}" fill="{color}" fill-rule="evenodd" '
                f'stroke="#222" stroke-width="0.025"><title>{escape(record["id"])}</title></path>'
            )
            p = part.representative_point()
            pieces.append(
                f'<text x="{p.x - x0}" y="{y1 - p.y}" font-size="0.65" fill="#111">{number}</text>'
            )
        record["overlay_number"] = number
    pieces.append("</svg>")
    path.write_text("\n".join(pieces), encoding="utf-8")


def _provenance() -> dict:
    root = Path(__file__).resolve().parent.parent

    def git(*args):
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else "unavailable"

    return {
        "git_commit": git("rev-parse", "HEAD"),
        "git_status": git("status", "--short"),
        "code_sha256": {
            p.name: _sha(p) for p in sorted((root / "memorymap_pipeline").glob("*.py"))
        },
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "shapely", "trimesh", "geopandas", "scipy")
        },
    }


def _finish(output: Path, report: dict) -> Path:
    report.update(
        schema_version=1,
        created_at=datetime.now(UTC).isoformat(),
        provenance=_provenance(),
        acceptance="pending_external_evidence",
    )
    _write(output / "report.json", report)
    evidence = {
        "schema_version": 1,
        "report_sha256": _sha(output / "report.json"),
        "printer": "",
        "material": "",
        "slicer": "Bambu Studio",
        "slicer_version": "",
        "nozzle_diameter_mm": 0.4,
        "layer_height_mm": 0.16,
        "slicer_project": "",
        "sliced_toolpaths": "",
        "print_photos": [],
        "accepted_omissions": [],
        "observations": [],
    }
    for specimen in report["specimens"]:
        for record in specimen["records"]:
            evidence["observations"].append(
                {
                    "specimen": specimen["name"],
                    "id": record["id"],
                    "slicer": "pending",
                    "physical_print": "pending",
                    "barriers": "pending",
                    "notes": "",
                }
            )
    _write(output / "evidence.json", evidence)
    lines = [
        f"# {report['name']}",
        "",
        "Status: **pending slicing and physical print**.",
        "",
        "Red footprints are geometric screening flags, not confirmed slicing failures.",
        "SVG labels match overlay_number in report.json. Coordinates and crops are millimeters.",
        "Crop edges create artificial cut faces; inspect interior features separately.",
        "",
        "| Specimen | Mapped footprints | Flagged | Model | Overlay |",
        "|---|---:|---:|---|---|",
    ]
    for specimen in report["specimens"]:
        count = sum(r.get("screening", {}).get("status") == "at_risk" for r in specimen["records"])
        name = specimen["name"]
        model_link = (
            f"[{name}.3mf]({name}.3mf)"
            if specimen.get("model_sha256")
            else "Export rejected (see report)"
        )
        lines.append(
            f"| {name} | {len(specimen['records'])} | {count} | "
            f"{model_link} | [{name}.svg]({name}.svg) |"
        )
    lines += [
        "",
        "Fill evidence.json after inspecting the saved Bambu project/toolpaths and printing.",
        "Run `python -m memorymap_pipeline.footprint_benchmark review <run-directory>`.",
        "Intentional omissions require explicit reasons; unresolved geometry prevents acceptance.",
    ]
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output / "report.json"


def run(manifest_path: Path, output: Path, label: str = "baseline", *, grouping: bool = False) -> Path:
    # Cap triangulation imports these lazily; fail before expensive generation.
    for dependency in ("scipy", "rtree"):
        importlib.import_module(dependency)
    manifest = _read(manifest_path)
    if manifest["schema_version"] != 1:
        raise ValueError("Unsupported benchmark schema")
    sources = {}
    for role in ("route", "buildings", "roads", "water"):
        entry = manifest["sources"][role]
        sources[role] = _inside(manifest_path.parent, entry["path"])
        if _sha(sources[role]) != entry["sha256"]:
            raise ValueError(f"Source checksum mismatch: {role}")
    frame = MapFrame(**manifest["frame"])
    config = deepcopy(manifest["config"])
    if grouping:
        config["building_grouping_enabled"] = True
    if config["style_profile"] != "urban":
        raise ValueError("Benchmark requires the frozen urban frame")
    if frame.margin_mm and not config["flat_border_enabled"]:
        raise ValueError("A frame with physical trim requires flat_border_enabled")
    output.mkdir(parents=True, exist_ok=False)
    diagnostics: list[dict] = []
    lat, lon = frame.print_to_lonlat(np.array([0, 190]), np.array([0, 240]))
    grid = ElevationGrid(
        np.zeros((32, 32)),
        float(lat.min()),
        float(lat.max()),
        float(lon.min()),
        float(lon.max()),
        "benchmark-flat-control",
    )
    result = generate_memory_map(
        GenerationRequest(
            route=load_route_from_gpx(sources["route"]),
            frame=frame,
            output_path=output / "full-map.3mf",
            config=config,
            roads_file=sources["roads"],
            buildings_file=sources["buildings"],
            water_file=sources["water"],
            elevation_grid=grid,
            building_diagnostics=diagnostics,
            export_model=False,
        ),
        progress_callback=lambda percent, message: print(f"{percent}% {message}", flush=True),
    )
    _write(output / "source-mapping.json", diagnostics)
    np.savez_compressed(
        output / "layer-meshes.npz",
        **{
            f"{layer}_{field}": getattr(mesh, field)
            for layer, mesh in result.meshes.items()
            for field in ("vertices", "faces")
        },
    )
    if result.buildings_mesh is not None:
        np.savez_compressed(
            output / "building-mesh.npz",
            vertices=result.buildings_mesh.vertices,
            faces=result.buildings_mesh.faces,
        )
    full_export = _attempt_export(output / "full-map.3mf", result.meshes)
    _write(output / "full-export.json", full_export)
    specimens = []
    for crop in manifest["crops"]:
        name, bounds = _name(crop["name"]), _bounds(crop["bounds_mm"])
        print(f"Cropping {name}", flush=True)
        try:
            meshes = {
                layer: cropped
                for layer, mesh in result.meshes.items()
                if (cropped := crop_mesh(mesh, bounds)) is not None
            }
            export = _attempt_export(output / f"{name}.3mf", meshes)
        except ValueError as exc:
            meshes = {}
            export = {
                "export_error": f"Crop failed: {exc}",
                "model_sha256": None,
                "export_removed_building_faces": None,
            }
        records = []
        for record in diagnostics:
            geometry = record.get("output_print_geometry") or record.get("source_print_geometry")
            if geometry is None or not shape(geometry).intersects(box(*bounds)):
                continue
            item = deepcopy(record)
            clipped = shape(geometry).intersection(box(*bounds))
            if clipped.area <= 0:
                continue
            item["screening"] = screen_footprint(clipped)
            item["cut_by_crop"] = not box(*bounds).covers(shape(geometry))
            records.append(item)
        _overlay(output / f"{name}.svg", bounds, records, meshes)
        specimens.append(
            {
                "name": name,
                "bounds_mm": bounds,
                "records": records,
                **export,
            }
        )
    _write(output / "source-mapping.json", diagnostics)
    shutil.copyfile(manifest_path, output / "input-manifest.json")
    return _finish(
        output,
        {
            "name": manifest["name"],
            "label": label,
            "manifest_sha256": _sha(manifest_path),
            "frame": manifest["frame"],
            "profile": manifest["profile"],
            "warnings": result.warnings,
            "generation_config": config,
            "building_grouping": result.stats.get("building_grouping"),
            "unresolved_source_ids": [r["id"] for r in diagnostics if r["status"] == "unresolved"],
            "full_export_removed_building_faces": full_export["export_removed_building_faces"],
            "full_export_error": full_export["export_error"],
            "full_model_sha256": full_export["model_sha256"],
            "specimens": specimens,
        },
    )


def synthetic(output: Path) -> Path:
    """Create deterministic print-space challenge specimens with known dimensions."""
    output.mkdir(parents=True, exist_ok=False)
    cases = {}
    for width in (0.2, 0.4, 0.6, 0.8, 1.2):
        cases[f"square-{width:g}"] = [box(4, 4, 4 + width, 4 + width)]
    cases.update(
        {
            "thin-rectangle": [box(4, 4, 12, 4.3)],
            "narrow-neck": [unary_union([box(3, 3, 6, 6), box(9, 3, 12, 6), box(6, 4, 9, 4.3)])],
            "courtyard-wall": [box(3, 3, 10, 10).difference(box(3.3, 3.3, 9.7, 9.7))],
            "close-neighbors": [box(3, 3, 3.4, 3.4), box(3.6, 3, 4, 3.4)],
            "boundary-fragment": [box(-1, 3, 0.2, 7).intersection(box(0, 0, 16, 16))],
            "height-transition": [box(3, 3, 6, 6), box(6.2, 3, 6.6, 3.4)],
        }
    )
    specimens = []
    for index, (case, polygons) in enumerate(cases.items(), 1):
        name = f"{index:02d}-{case.replace('.', 'p')}"
        records, bodies = [], []
        for number, polygon in enumerate(polygons, 1):
            height = 8.0 if case == "height-transition" and number == 2 else 4.0
            bodies.append(route_mesh_from_polygon(polygon, height + 0.2, -0.2))
            records.append(
                {
                    "id": f"{name}-b{number}",
                    "source_id": f"synthetic:{case}:{number}",
                    "status": "retained",
                    "reason": "synthetic_control",
                    "visible_height_mm": height,
                    "output_print_geometry": mapping(polygon),
                    "screening": screen_footprint(polygon),
                }
            )
        meshes = {"base": build_base_plate(16, 16, 1.6), "buildings": concatenate(bodies)}
        audit = audit_printability(meshes)
        _export(output / f"{name}.3mf", meshes)
        _overlay(output / f"{name}.svg", (0, 0, 16, 16), records)
        specimens.append(
            {
                "name": name,
                "bounds_mm": [0, 0, 16, 16],
                "records": records,
                "structural_audit_issues": [asdict(issue) for issue in audit.issues],
                "model_sha256": _sha(output / f"{name}.3mf"),
            }
        )
    return _finish(
        output,
        {
            "name": "Synthetic footprint controls v1",
            "label": "baseline",
            "profile": PROFILE,
            "manifest_sha256": "synthetic-v1",
            "specimens": specimens,
        },
    )


def review(directory: Path) -> dict:
    """Require complete, traceable external evidence; missing observations never pass."""
    report = _read(directory / "report.json")
    evidence = _read(directory / "evidence.json")
    problems = []
    if report.get("unresolved_source_ids"):
        problems.append("Source geometry remains unresolved; inspect source-mapping.json")
    if report.get("full_export_error"):
        problems.append("Full-map export rejected: " + report["full_export_error"])
    if report.get("full_export_removed_building_faces", 0):
        problems.append("Full-map export changed building faces; reconcile source mapping")
    if evidence.get("report_sha256") != _sha(directory / "report.json"):
        problems.append("Evidence belongs to a different or modified report")
    for key in ("printer", "material", "slicer_version"):
        if not evidence.get(key):
            problems.append(f"Missing {key}")
    for key in ("nozzle_diameter_mm", "layer_height_mm"):
        if evidence.get(key) != report["profile"][key]:
            problems.append(f"Profile mismatch: {key}")
    files = {}
    for key in ("slicer_project", "sliced_toolpaths", "print_photos"):
        values = evidence.get(key)
        values = values if isinstance(values, list) else [values]
        if not values:
            problems.append(f"Missing {key}")
        for value in values:
            if not value or not _inside(directory, value).is_file():
                problems.append(f"Missing evidence artifact: {key} {value}")
            else:
                files[value] = _sha(_inside(directory, value))
    observations = {}
    for item in evidence.get("observations", []):
        key = (item["specimen"], item["id"])
        if key in observations:
            problems.append(f"Duplicate observation: {key}")
        observations[key] = item
    omissions = {
        (i["specimen"], i["id"]): i.get("reason", "").strip()
        for i in evidence.get("accepted_omissions", [])
    }
    expected = set()
    for specimen in report["specimens"]:
        if specimen.get("export_removed_building_faces", 0):
            problems.append(f"Export changed building faces: {specimen['name']}")
        if specimen.get("export_error"):
            problems.append(f"{specimen['name']}: {specimen['export_error']}")
        model = directory / (specimen["name"] + ".3mf")
        if not model.is_file() or _sha(model) != specimen["model_sha256"]:
            problems.append(f"Model changed: {specimen['name']}")
        if not specimen["records"]:
            problems.append(f"Empty specimen: {specimen['name']}")
        if not any(r["status"] in {"retained", "grouped"} for r in specimen["records"]):
            problems.append(f"No retained masses: {specimen['name']}")
        for record in specimen["records"]:
            key = (specimen["name"], record["id"])
            expected.add(key)
            if record["status"] not in {"retained", "grouped", "omitted", "unresolved"}:
                problems.append(f"Unknown disposition: {key}")
            elif record["status"] == "unresolved":
                problems.append(f"Unresolved geometry: {key}")
            elif record["status"] == "omitted":
                if not omissions.get(key):
                    problems.append(f"Unacknowledged omission: {key}")
            else:
                observation = observations.get(key, {})
                for stage in ("slicer", "physical_print", "barriers"):
                    if observation.get(stage) != "pass":
                        problems.append(f"{key}: {stage} is not pass")
    if set(observations) != expected:
        problems.append("Observation IDs do not match the immutable report")
    result = {
        "status": "accepted" if not problems else "not_accepted",
        "basis": "operator-recorded evidence; not independent physical verification",
        "problems": problems,
        "evidence_sha256": _sha(directory / "evidence.json"),
        "artifacts_sha256": files,
    }
    _write(directory / "review.json", result)
    return result


def compare(baseline: Path, candidate: Path) -> dict:
    first, second = _read(baseline), _read(candidate)
    if first["manifest_sha256"] != second["manifest_sha256"]:
        raise ValueError("Cannot compare different source snapshots")

    def rows(report):
        return {(s["name"], r["id"]): r for s in report["specimens"] for r in s["records"]}

    before, after = rows(first), rows(second)
    return {
        "missing_source_ids": sorted(set(before) - set(after)),
        "added_source_ids": sorted(set(after) - set(before)),
        "changes": [
            {
                "specimen": key[0],
                "id": key[1],
                "before": before[key]["status"],
                "after": after[key]["status"],
                "screening_before": before[key].get("screening"),
                "screening_after": after[key].get("screening"),
            }
            for key in sorted(set(before) & set(after))
            if before[key] != after[key]
        ],
        "acceptance": "requires separate slicing and physical evidence",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("synthetic")
    p.add_argument("output", type=Path)
    p = commands.add_parser("freeze")
    p.add_argument("spec", type=Path)
    p.add_argument("output", type=Path)
    p = commands.add_parser("run")
    p.add_argument("manifest", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--label", default="baseline")
    p.add_argument("--group-buildings", action="store_true")
    p = commands.add_parser("review")
    p.add_argument("directory", type=Path)
    p = commands.add_parser("compare")
    p.add_argument("baseline", type=Path)
    p.add_argument("candidate", type=Path)
    args = parser.parse_args()
    if args.command == "synthetic":
        print(synthetic(args.output))
    elif args.command == "freeze":
        print(freeze(args.spec, args.output))
    elif args.command == "run":
        print(run(args.manifest, args.output, args.label, grouping=args.group_buildings))
    elif args.command == "review":
        result = review(args.directory)
        print(json.dumps(result, indent=2))
        if result["status"] != "accepted":
            raise SystemExit(1)
    else:
        print(json.dumps(compare(args.baseline, args.candidate), indent=2))


if __name__ == "__main__":
    main()
