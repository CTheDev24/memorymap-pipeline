"""Build a portable review of actual frozen-source renders and diagnostics."""

import base64
import html
import json
from pathlib import Path

root = Path("benchmarks/outputs/city-relief")
rows = []
sections = []
for area in ("downtown", "neighborhood", "landmark"):
    cards = []
    for mode in ("existing", "balanced", "dense"):
        folder = root / "prototypes" / area / mode
        metrics = json.loads((folder / "metrics.json").read_text())
        stats = metrics["statistics"]["building_generalization"]
        values = {
            key: stats.get(key)
            for key in (
                "source_building_count",
                "represented_source_count",
                "output_representative_count",
                "omitted_count",
                "unresolved_count",
            )
        }
        rows.append(dict(area=area, mode=mode, seconds=metrics["elapsed_seconds"], **values))
        views = []
        for camera in ("top", "perspective", "side"):
            pixels = base64.b64encode((folder / f"{camera}.png").read_bytes()).decode()
            views.append(
                f'<figure data-camera="{camera}"><img src="data:image/png;base64,{pixels}"><figcaption>{camera}</figcaption></figure>'
            )
        cards.append(
            f"<article><h3>{mode.title()}</h3>{''.join(views)}<p>{values['represented_source_count']:,} sources represented; {values['output_representative_count']:,} output forms; {values['unresolved_count']:,} unresolved.</p><p>Generation: {metrics['elapsed_seconds']:.1f} s</p></article>"
        )
    sections.append(
        f'<section><h2>{area.title()} — 24 × 24 mm</h2><div class="grid">{"".join(cards)}</div></section>'
    )
page = """<!doctype html><meta charset="utf-8"><title>LA City relief review</title>
<style>body{font:16px system-ui;background:#f6f4ee;color:#20272d;margin:32px;max-width:1600px}h1{font-size:32px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}article{background:white;padding:16px;border-radius:10px}figure{margin:0}img{width:100%}figcaption{color:#555}figure[data-camera]{display:none}body[data-view="perspective"] figure[data-camera="perspective"],body[data-view="top"] figure[data-camera="top"],body[data-view="side"] figure[data-camera="side"]{display:block}button{padding:10px 20px;margin:4px;cursor:pointer}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style>
<body data-view="perspective"><h1>LA City relief: prototype review</h1>
<p>Actual exported geometry, frozen LA sources, identical cameras. Each square represents approximately 2.291 km at the full 240 × 190 mm map scale: 95.45 metres per printed millimetre. Outer-wall feature budget: 0.42 mm with a 0.4 mm nozzle.</p>
<p>City relief is illustrative. The route stays geographically accurate. Local streets appear near the route; major roads remain visible across the map. Internal street, route, park and water barriers constrain buildings independently of visible roads.</p>
<p><strong>Acceptance remains pending.</strong> Preserved thin assemblies remain unresolved. These renders do not establish toolpath survival, strength or physical print quality. Runtime samples were generated alongside other validation jobs; they are diagnostic, not an isolated release benchmark.</p>
<nav><button onclick="document.body.dataset.view='top'">Top</button><button onclick="document.body.dataset.view='perspective'">Perspective</button><button onclick="document.body.dataset.view='side'">Side</button></nav>
""" + "".join(sections)
slice_report = root / "slicer" / "toolpath-survival.json"
if slice_report.exists():
    page += "<h2>Actual A1 slicing</h2><p>0.4 mm nozzle, 0.42 mm outer walls, 0.16 mm layers, PLA Basic. Centerline presence above the base is evidence of toolpaths, not a strength or clearance guarantee.</p><ul>"
    for sample in json.loads(slice_report.read_text()):
        page += f"<li>{html.escape(sample['area'].title())}: {sample['forms_with_toolpaths']} / {sample['output_forms']} forms receive above-base toolpaths; {sample['seconds'] / 60:.1f} min; {sample['grams']:.2f} g.</li>"
    page += "</ul><p>All 28 typified neighborhood forms receive toolpaths. Only 62 of 199 unresolved preserved structures do. Physically print and inspect the coupons before accepting the preset.</p>"
page += "</body>"
(root / "review.html").write_text(page, encoding="utf-8")
(root / "prototype-summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
print(root / "review.html")
