from PyInstaller.utils.hooks import collect_data_files, copy_metadata

datas = collect_data_files("memorymap_pipeline.desktop") + copy_metadata("osmnx", recursive=True)
a = Analysis(
    ["memorymap_pipeline/desktop/__main__.py"], pathex=[], binaries=[], datas=datas,
    hiddenimports=["PySide6.QtWebChannel", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="MemoryMap", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=True, console=False)
