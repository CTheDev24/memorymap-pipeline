"""Package the bounded recovery checkpoint without the rejected full-map payload."""

import base64
import html
import json
import zipfile
from pathlib import Path

root = Path("benchmarks/outputs/city-relief")
page = """<!doctype html><meta charset="utf-8"><title>City relief recovery review</title>
<style>body{font:16px system-ui;margin:32px;background:#f6f4ee;color:#20272d}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}article{background:white;padding:16px}img{width:100%}figure{margin:0}figure[data-view]{display:none}body[data-view="perspective"] figure[data-view="perspective"],body[data-view="top"] figure[data-view="top"],body[data-view="side"] figure[data-view="side"]{display:block}button{padding:10px;margin:5px}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style>
<body data-view="perspective"><h1>LA City relief: recovery checkpoint</h1>
<p>Actual exported geometry at the full LA map scale: 95.45 metres per printed millimetre. Each coupon is 24 × 24 mm. Existing and Dense are historical frozen-source comparisons; Balanced uses the new reviewed native package. Camera bounds are identical. Timings across these different builds are not controlled performance comparisons.</p>
<p>The original full City map remains rejected for missing Roads. This checkpoint tests full-frame roads separately and complete Balanced coupons. It does not establish full-map building completion or physical acceptance.</p>
<p>Balanced still looks sparse in the low-rise neighborhood: 28 typified masses plus 199 unresolved forms. Dense adds only four typified masses. High centroid-cell coverage is not proof of inhabited density. Landmark silhouette recognition remains unaccepted; route exclusion removes substantial geometry from City Hall and Concert Hall. No forced enlargement or synthetic infill was added.</p>
<nav><button onclick="document.body.dataset.view='top'">Top</button><button onclick="document.body.dataset.view='perspective'">Perspective</button><button onclick="document.body.dataset.view='side'">Side</button></nav>
"""
for area in ("downtown", "neighborhood", "landmark"):
    cards = []
    for mode in ("existing", "balanced", "dense"):
        folder = root / (
            f"supported-prototypes/{area}/balanced"
            if mode == "balanced"
            else f"prototypes/{area}/{mode}"
        )
        figures = []
        for camera in ("top", "perspective", "side"):
            encoded = base64.b64encode((folder / f"{camera}.png").read_bytes()).decode()
            figures.append(
                f'<figure data-view="{camera}"><img src="data:image/png;base64,{encoded}"><figcaption>{camera}</figcaption></figure>'
            )
        audit = json.loads((folder / "sample.audit.json").read_text())
        stats = audit["statistics"]["building_generalization"]
        counts = "; ".join(
            f"{label}: {stats.get(key, 'unavailable')}"
            for label, key in (
                ("sources represented", "represented_source_count"),
                ("output forms", "output_representative_count"),
                ("omitted", "omitted_count"),
                ("unresolved", "unresolved_count"),
            )
        )
        cards.append(
            f"<article><h3>{mode.title()}{' — current package' if mode == 'balanced' else ' — historical'}</h3>{''.join(figures)}<p>{html.escape(counts)}</p></article>"
        )
    page += f'<h2>{area.title()}</h2><div class="grid">{"".join(cards)}</div>'
paths = json.loads((root / "supported-slicer/toolpath-survival.json").read_text())
page += "<h2>Current A1 slices</h2><p>0.4 mm nozzle, 0.42 mm outer line width, 0.16 mm layers, PLA Basic. Toolpath presence does not prove silhouettes, gaps, strength, or physical acceptance.</p><ul>"
for row in paths:
    page += f"<li>{row['area']}: {row['forms_with_toolpaths']} / {row['output_forms']} forms have sampled above-base toolpaths; {row['seconds'] / 60:.2f} minutes, {row['grams']:.2f} g.</li>"
page += "</ul><p>Inspect distinct masses, unintended merges, fragile or missing details, route and road clarity, and landmark recognition at normal viewing distance and under side lighting. Record owner acceptance separately for each coupon. The neighborhood crop cannot assess a route outside its frame.</p></body>"
(root / "supported-review.html").write_text(page, encoding="utf-8")
target = root / "city-relief-recovery-review.zip"
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for name in (
        "city-relief-exact-recovery.md",
        "city-relief-exact-recovery-metrics.json",
        "city-relief-print-checklist.md",
    ):
        archive.write(Path("docs") / name, name)
    for pattern in ("supported-*.json", "supported-review.html", "tests-support-proof.log"):
        for path in root.glob(pattern):
            archive.write(path, path.relative_to(root))
    for area in ("downtown", "neighborhood", "landmark"):
        for path in (root / f"supported-prototypes/{area}/balanced").glob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(root))
        for path in (root / "supported-slicer").glob(f"{area}-final-*"):
            archive.write(path, path.relative_to(root))
    archive.write(
        root / "supported-slicer/toolpath-survival.json", "supported-slicer/toolpath-survival.json"
    )
    for name in ("machine.json", "process.json", "filament.json"):
        archive.write(root / "slicer" / name, f"profiles/{name}")
    for path in (root / "supported-full-roads").glob("la.*"):
        if path.suffix != ".glb":
            archive.write(path, path.relative_to(root))
    for path in (root / "road-diagnostic").glob("indexed-exact-regions.json"):
        archive.write(path, path.relative_to(root))
print(target, target.stat().st_size)
