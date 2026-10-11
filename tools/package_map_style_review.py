"""Package generated style comparisons and explicit manual acceptance tasks."""
import base64
import html
import json
import sys
import zipfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
report = json.loads((root / "report.json").read_text(encoding="utf-8"))
sections = []
for style, case in report["cases"].items():
    pictures = []
    for view in ("top", "perspective", "side"):
        columns = []
        for label, path in (("Baseline", root / style / "baseline" / f"{view}.png"),
                            ("Candidate", root / style / f"{view}.png")):
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            columns.append(f'<figure><figcaption>{label} — {view}</figcaption><img src="data:image/png;base64,{data}"></figure>')
        pictures.append('<div class="pair">' + ''.join(columns) + '</div>')
    selected = case["statistics"]["road_processing"].get("selection", {})
    summary = f'{case["seconds"]:.2f} s generation; base/route/buildings/terrain/water arrays match the baseline.'
    if selected:
        summary += f' Retained {selected["retained_count"]} of {selected["source_count"]} source corridors.'
    sections.append(f'<section><h2>{html.escape(style.replace("_", " ").title())}</h2><p>{summary}</p>' + ''.join(pictures) + '</section>')
page = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Trace Map Style review</title>
<style>body{background:#252b31;color:#eee;font:16px system-ui;max-width:1200px;margin:auto;padding:32px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}figure{margin:0}img{width:100%}figcaption{padding:8px}
section{border-top:1px solid #666;margin-top:32px}p{line-height:1.6}</style>
<h1>Trace Map Style regression review</h1><p>Representative offline data at 240 × 190 mm.
These are software regression samples, not Houston, Big Sur or Chicago race models.
Urban road widths and separate parts change intentionally; other geometry remains identical.</p>
<p>Native depth-buffered preview cameras match between baseline and candidate. These are geometry views,
not slicer or toolpath previews. Bambu Studio material assignment and physical print acceptance are pending.</p>''' + ''.join(sections) + '''
<h2>Manual acceptance</h2><ol><li>Import each candidate.3mf into Bambu Studio at 100% scale as one assembly.
Verify 240 × 190 mm registration and all named road parts.</li><li>Assign one road class to a different
filament, then assign several classes to the same filament. Save the project and record the Bambu version/profile.</li>
<li>Slice with the actual printer and line width. Inspect class intersections, route clearance, surviving narrow
roads, topology warnings, gaps and merged forms.</li><li>Print small regions first. Inspect route prominence,
street continuity, building height/massing and terrain/water alignment under normal viewing and side lighting.</li>
<li>Review a real large-area street grid before approving these experimental spacing and width defaults.</li></ol></html>'''
(root / "review.html").write_text(page, encoding="utf-8")
archive_path = root / "map-style-review.zip"
with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != archive_path:
            archive.write(path, path.relative_to(root))
with zipfile.ZipFile(archive_path) as archive:
    assert archive.testzip() is None
print(archive_path)
