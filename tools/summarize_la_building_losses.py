"""Stream full-map building dispositions without loading gigabyte audits."""

import json
from collections import Counter
from pathlib import Path


def records(path):
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.rstrip() == '  "buildings": [':
                break
        else:
            raise ValueError(f"Missing top-level building records: {path}")
        buffer = ""
        while True:
            buffer = buffer.lstrip(" \t\r\n,")
            if buffer.startswith("]"):
                return
            try:
                value, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                block = stream.read(1024 * 1024)
                if not block:
                    raise ValueError(f"Incomplete building audit: {path}")
                buffer += block
                continue
            yield value
            buffer = buffer[end:]


root = Path("benchmarks/outputs/city-relief")
reports = {}
for folder in ("existing", "packaged-full"):
    path = root / folder / "la.audit.json"
    if not path.exists():
        continue
    reasons, statuses, actions = Counter(), Counter(), Counter()
    metrics_path = root / (
        "packaged-full-report.json" if folder == "packaged-full" else "existing/metrics.json"
    )
    metrics = json.loads(metrics_path.read_text())
    face_count = metrics["statistics"]["layers"]["buildings"]["faces"]
    invalid_ranges = missing_output = 0
    total = with_faces = 0
    for row in records(path):
        total += 1
        with_faces += bool(row.get("output_face_ranges"))
        invalid_ranges += any(
            not (0 <= start < end <= face_count) for start, end in row.get("output_face_ranges", [])
        )
        missing_output += row.get("status") in {"retained", "grouped"} and not row.get(
            "output_face_ranges"
        )
        reasons[row.get("reason", "unspecified")] += 1
        statuses[row.get("status", "unspecified")] += 1
        actions[row.get("optimization", {}).get("action", "unspecified")] += 1
    reports[folder] = {
        "audited_polygon_records": total,
        "records_with_output_face_ranges": with_faces,
        "audited_mesh_face_count": face_count,
        "records_with_out_of_bounds_face_ranges": invalid_ranges,
        "represented_records_without_faces": missing_output,
        "status_counts": dict(statuses),
        "reason_counts": dict(reasons),
        "optimization_action_counts": dict(actions),
        "caveat": "Original Existing baseline predates export cleanup reconciliation; its face ranges describe pre-cleanup geometry. Element, polygon, and representative counts use distinct units.",
    }
    if folder == "packaged-full":
        assert not invalid_ranges and not missing_output, (
            "Final source/output audit mappings are invalid"
        )
(root / "building-loss-breakdown.json").write_text(json.dumps(reports, indent=2))
print(json.dumps(reports, indent=2))
