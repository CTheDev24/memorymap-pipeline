"""Summarize completed bounded proofs; never promote them to full-map acceptance."""

import json
from pathlib import Path

root = Path("benchmarks/outputs/city-relief")
target = Path("docs/city-relief-exact-recovery-metrics.json")


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


metrics = read(target)
metrics["exact_road_replay"] = read(root / "road-diagnostic/indexed-exact-regions.json")
provenance = read(root / "supported-package-provenance.json")
metrics["review_executable_sha256"] = provenance["executable_sha256"]
metrics["package_matches_source"] = provenance["passed"]
metrics["frozen_inputs"] = read(root / "supported-frozen-input-verification.json")
assert metrics["frozen_inputs"]["all_unchanged"]
metrics["support_module_provenance"] = provenance["modules"]["memorymap_pipeline.printability"]
metrics["identity_scope"] = (
    "Audit source_sha256 is the legacy selected geometry-module fingerprint; use executable and verified module hashes for complete review-package identity."
)
metrics["startup_smoke_passed"] = read(root / "supported-package-startup-smoke.json")["passed"]
rows = []
for area in ("full-roads", "downtown", "neighborhood", "landmark"):
    report = read(root / f"supported-{area}-report.json")
    performance = read(root / f"supported-{area}-report.performance.json")
    verified = read(root / f"supported-{area}-verification.json")
    assert report["passed"] and verified["passed"]
    assert performance["executable_sha256"] == provenance["executable_sha256"]
    stats = report["statistics"]
    general = stats.get("building_generalization", {})
    rows.append(
        {
            "area": area,
            "passed": True,
            "generation_seconds": report["elapsed_seconds"],
            "wall_seconds": performance["elapsed_wall_seconds"],
            "peak_process_working_set_bytes": performance["peak_process_working_set_bytes"],
            "model_sha256": verified["model_sha256"],
            "build": stats["build"],
            "layer_states": stats["layer_states"],
            "layers": stats["layers"],
            "source_accounting": {
                key: general.get(key)
                for key in (
                    "acquired_source_count",
                    "decoded_polygon_count",
                    "in_frame_element_count",
                    "source_building_count",
                    "represented_source_count",
                    "output_representative_count",
                    "omitted_count",
                    "unresolved_count",
                )
            },
            "building_stage_seconds": general.get("stage_seconds", {}),
            "neighborhood_coverage": general.get("neighborhood_coverage", {}),
            "previous_geometry_identical": verified.get("previous_geometry_identical", {}),
            "warnings": report["warnings"],
        }
    )
metrics["bounded_native_validation"] = rows
metrics["native_timing_caveat"] = (
    "The separately installed Trace Studio was active during this native validation. Historical native timings are uncontrolled; the serial, identical-input three-repeat typification replays establish the measured cache effect."
)
existing = []
for area in ("downtown", "neighborhood", "landmark"):
    verified = read(root / f"supported-existing-{area}-verification.json")
    assert verified["passed"] and all(
        value
        for layer, value in verified["previous_geometry_identical"].items()
        if layer != "roads"
    )
    road_change = root / f"supported-existing-{area}-road-difference.json"
    if not verified["previous_geometry_identical"].get("roads", True):
        assert read(road_change)["passed"]
    existing.append(
        {
            "area": area,
            "identical_layers": verified["previous_geometry_identical"],
            "model_sha256": verified["model_sha256"],
            "road_repair": read(road_change) if road_change.exists() else None,
        }
    )
metrics["existing_compatibility"] = existing
metrics["current_slicer_evidence"] = read(root / "supported-slicer/toolpath-survival.json")
metrics["acceptance"] = {
    "bounded_native_checks_passed": True,
    "full_building_retry_started": False,
    "full_map_accepted": False,
    "physical_acceptance": "pending owner review",
    "full_run_performance": "unmeasured after these changes",
}
target.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
print(
    json.dumps(
        {
            "native": [
                (r["area"], r["generation_seconds"], r["peak_process_working_set_bytes"])
                for r in rows
            ],
            "existing_compatible": True,
            "acceptance": metrics["acceptance"],
        },
        indent=2,
    )
)
