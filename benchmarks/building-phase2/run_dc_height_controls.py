"""Offline Pentagon regression at the DC Half Marathon's fitted print scale."""
import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("gpx", type=Path)
    parser.add_argument("snapshot", type=Path, help="Frozen Pentagon GeoJSON from cached OSM relation 89605")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks/outputs/dc-height-controls")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["LOCALAPPDATA"] = str(out / "appdata")
    os.environ["MPLCONFIGDIR"] = str(out / "mpl-cache")
    from unittest.mock import patch

    import geopandas as gpd
    import numpy as np

    from memorymap_pipeline.building_heights import BuildingHeightPolicy
    from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
    from memorymap_pipeline.gpx_loader import load_route_from_gpx
    from memorymap_pipeline.map_frame import MapFrame
    from memorymap_pipeline.printability import audit_printability

    route = load_route_from_gpx(args.gpx)
    frame = MapFrame.fit_route(route.points, 190, 240, margin_mm=0, route_padding_mm=5.6)
    features = json.loads(args.snapshot.read_text(encoding="utf-8"))
    assert len(features["features"]) == 1
    tags = features["features"][0]["properties"]
    assert tags["name"] == "The Pentagon" and int(tags["id"]) == 89605
    source_height = float(tags["height"])
    source = gpd.GeoDataFrame.from_features(features["features"], crs="EPSG:4326")
    cases = {}
    footprint = None
    for name, policy in (
        ("legacy", BuildingHeightPolicy()),
        ("proportional-1x", BuildingHeightPolicy("proportional")),
        ("proportional-2x", BuildingHeightPolicy("proportional", 2)),
        ("proportional-capped", BuildingHeightPolicy("proportional", 2, None, 1.2)),
    ):
        records = []
        with patch("geopandas.read_file", return_value=source.copy()):
            result = generate_memory_map(GenerationRequest(
                route=route, frame=frame, output_path=out / f"pentagon-{name}.3mf",
                include_roads=False, buildings_file=args.snapshot, building_diagnostics=records,
                config={**policy.config(), "terrain_enabled": False, "water_enabled": False,
                        "flat_border_enabled": False, "max_print_height_mm": 26},
            ))
        mesh = result.buildings_mesh
        assert mesh is not None
        assert all(m.is_watertight and m.is_winding_consistent for m in result.meshes.values())
        assert audit_printability(result.meshes).printable
        visible_top = float(mesh.bounds[1, 2])
        if policy.active:
            expected = policy.resolve(source_height * frame.mm_per_meter)[0]
            assert abs(visible_top - expected) < 1e-6, (visible_top, expected)
            audit = json.loads(result.output_path.with_suffix(".audit.json").read_text(encoding="utf-8"))
            assert audit["settings"]["building_height_multiplier"] == policy.multiplier
            assert audit["model_sha256"] == hashlib.sha256(result.output_path.read_bytes()).hexdigest()
        xy = np.sort(np.unique(mesh.vertices[:, :2], axis=0), axis=0)
        if footprint is None:
            footprint = xy
        else:
            assert np.array_equal(footprint, xy), "Height controls changed footprint"
        cases[name] = {"policy": policy.config(), "visible_top_mm": visible_top,
                       "faces": len(mesh.faces), "support_gate_passed": True,
                       "watertight": True, "records": records,
                       "model_sha256": hashlib.sha256(result.output_path.read_bytes()).hexdigest()}
        print(name, visible_top, flush=True)
    report = {"frame": asdict(frame), "source_height_m": source_height,
              "source_osm_id": "relation/89605", "snapshot_sha256": hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
              "gpx_sha256": hashlib.sha256(args.gpx.read_bytes()).hexdigest(), "cases": cases,
              "limitations": ["Focused Pentagon fixture, not a complete DC regeneration",
                              "Auto-fitted frame; original saved model settings are unavailable",
                              "No slicer or physical print validation"]}
    (out / "height-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
