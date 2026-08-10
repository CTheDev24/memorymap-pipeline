from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


project_root = Path(SPECPATH)
package_root = project_root / "memorymap_pipeline"
datas = [
    (str(package_root / "data" / "landmarks.v1.json"), "memorymap_pipeline/data"),
    (str(package_root / "desktop" / "viewer"), "memorymap_pipeline/desktop/viewer"),
]
datas += collect_data_files("rasterio")
datas += copy_metadata("osmnx", recursive=True)
hiddenimports = [
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
] + collect_submodules("rasterio")
a = Analysis(
    ["memorymap_pipeline/desktop/__main__.py"], pathex=[], binaries=[], datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="MemoryMap", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=True, console=False)
