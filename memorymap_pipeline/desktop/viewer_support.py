"""Helpers for loading the packaged, offline 3D preview viewer."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode


VIEWER_ASSET_NAMES = (
    "index.html",
    "three.min.js",
    "OrbitControls.js",
    "GLTFLoader.js",
    "THREE-LICENSE.txt",
)


def viewer_directory() -> Path:
    """Return the package directory containing the offline viewer runtime."""

    return Path(__file__).resolve().parent / "viewer"


def viewer_url(preview_path: str | Path) -> str:
    """Return a local viewer URL for a generated GLB.

    The GLB path is supplied as a ``file:`` URL. Qt WebEngine is configured to
    allow local viewer content to read other local files.
    """

    preview = Path(preview_path).resolve()
    index = viewer_directory() / "index.html"
    if not preview.is_file():
        raise FileNotFoundError(f"3D preview does not exist: {preview}")
    missing = [name for name in VIEWER_ASSET_NAMES if not (index.parent / name).is_file()]
    if missing:
        raise FileNotFoundError(f"3D viewer assets are missing: {', '.join(missing)}")
    return f"{index.as_uri()}?{urlencode({'model': preview.as_uri()})}"


def placeholder_html(message: str = "Generate a map to view its 3D preview.") -> str:
    """Return a small offline placeholder for the preview tab."""

    safe = (
        str(message)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
    html,body{{height:100%;margin:0;background:#252b31;color:#dce3e7;font:14px system-ui}}
    body{{display:grid;place-items:center}}div{{text-align:center;max-width:420px;padding:24px}}
    </style></head><body><div>{safe}</div></body></html>"""


__all__ = [
    "VIEWER_ASSET_NAMES",
    "placeholder_html",
    "viewer_directory",
    "viewer_url",
]
