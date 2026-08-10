from __future__ import annotations

import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIEWER_DIRECTORY = PROJECT_ROOT / "memorymap_pipeline" / "desktop" / "viewer"


def test_embedded_viewer_assets_are_present_and_nonempty() -> None:
    index = VIEWER_DIRECTORY / "index.html"
    expected_scripts = [
        VIEWER_DIRECTORY / "three.min.js",
        VIEWER_DIRECTORY / "GLTFLoader.js",
        VIEWER_DIRECTORY / "OrbitControls.js",
    ]
    license_file = VIEWER_DIRECTORY / "THREE-LICENSE.txt"

    assert index.is_file()
    assert index.stat().st_size > 0
    assert all(script.is_file() and script.stat().st_size > 0 for script in expected_scripts)
    assert license_file.is_file()
    assert "MIT License" in license_file.read_text(encoding="utf-8")


def test_embedded_viewer_uses_only_local_script_sources() -> None:
    html = (VIEWER_DIRECTORY / "index.html").read_text(encoding="utf-8")

    assert "three.min.js" in html
    assert "GLTFLoader.js" in html
    assert "OrbitControls.js" in html
    assert "https://" not in html
    assert "http://" not in html


def test_embedded_viewer_assets_are_declared_as_package_data() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = pyproject["tool"]["setuptools"]["package-data"]["memorymap_pipeline"]

    assert "desktop/viewer/*.html" in patterns
    assert "desktop/viewer/*.js" in patterns
    assert "desktop/viewer/*.css" in patterns
    assert "desktop/viewer/*.txt" in patterns


def test_pyinstaller_collects_package_data_and_qt_webengine() -> None:
    spec = (PROJECT_ROOT / "memorymap-desktop.spec").read_text(encoding="utf-8")

    assert '"memorymap_pipeline/desktop/viewer"' in spec
    assert '"memorymap_pipeline/data"' in spec
    assert 'collect_data_files("rasterio")' in spec
    assert 'collect_submodules("rasterio")' in spec
    assert '"PySide6.QtWebEngineCore"' in spec
    assert '"PySide6.QtWebEngineWidgets"' in spec


def test_desktop_workflow_labels_and_checks_preview_artifact() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "build-desktop.yml"
    ).read_text(encoding="utf-8")

    assert "MemoryMap-Windows-Preview-${{ github.sha }}" in workflow
    assert r"memorymap_pipeline\.desktop\.preview" in workflow
    assert r"memorymap_pipeline[\\/]desktop[\\/]viewer[\\/]index\.html" in workflow
    assert r"PySide6[\\/]QtWebEngineWidgets\.(pyd|dll)" in workflow
