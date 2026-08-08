from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from memorymap_pipeline.desktop.viewer_support import (
    VIEWER_ASSET_NAMES,
    placeholder_html,
    viewer_directory,
    viewer_url,
)


def test_offline_viewer_assets_and_controls_are_packaged(tmp_path):
    asset_directory = viewer_directory()
    assert all((asset_directory / name).is_file() for name in VIEWER_ASSET_NAMES)

    html = (asset_directory / "index.html").read_text(encoding="utf-8")
    assert "https://" not in html
    assert "three.min.js" in html
    assert "GLTFLoader.js" in html
    assert "OrbitControls.js" in html
    assert 'data-camera="top"' in html
    assert 'data-camera="side"' in html
    assert 'id="layer-list"' in html
    assert "triangles" in html
    assert "camera.up.set(0, 0, 1)" in html

    preview = tmp_path / "map preview.glb"
    preview.write_bytes(b"glTF")
    url = viewer_url(preview)
    assert url.startswith((asset_directory / "index.html").as_uri())
    assert "model=file%3A" in url


def test_viewer_support_rejects_missing_preview_and_escapes_placeholder(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        viewer_url(tmp_path / "missing.glb")
    html = placeholder_html("<Preview & model>")
    assert "&lt;Preview &amp; model&gt;" in html


def test_generation_worker_returns_3mf_and_preview_paths(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from memorymap_pipeline import generation
    from memorymap_pipeline.desktop import preview
    from memorymap_pipeline.desktop.worker import GenerationWorker

    output = tmp_path / "memorymap.3mf"
    output.write_bytes(b"3mf")
    result = SimpleNamespace(
        output_path=output,
        warnings=["source warning"],
        meshes={"base": object()},
    )
    monkeypatch.setattr(generation, "GenerationRequest", lambda **data: data)
    monkeypatch.setattr(
        generation,
        "generate_memory_map",
        lambda request, progress_callback: result,
    )

    def export(meshes, destination):
        assert meshes is result.meshes
        Path(destination).write_bytes(b"glTF")
        return Path(destination)

    monkeypatch.setattr(preview, "export_preview_glb", export)
    completed = []
    warnings = []
    worker = GenerationWorker({"request": "value"})
    worker.completed.connect(lambda model, glb: completed.append((model, glb)))
    worker.warning.connect(warnings.append)

    worker.run()

    assert completed == [
        (str(output), str(tmp_path / "memorymap-preview.glb"))
    ]
    assert warnings == ["source warning"]


def test_generation_worker_keeps_valid_3mf_when_preview_fails(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from memorymap_pipeline import generation
    from memorymap_pipeline.desktop import preview
    from memorymap_pipeline.desktop.worker import GenerationWorker

    output = tmp_path / "memorymap.3mf"
    result = SimpleNamespace(output_path=output, warnings=[], meshes={"base": object()})
    monkeypatch.setattr(generation, "GenerationRequest", lambda **data: data)
    monkeypatch.setattr(
        generation,
        "generate_memory_map",
        lambda request, progress_callback: result,
    )
    monkeypatch.setattr(
        preview,
        "export_preview_glb",
        lambda meshes, destination: (_ for _ in ()).throw(RuntimeError("renderer failed")),
    )
    completed = []
    warnings = []
    worker = GenerationWorker({})
    worker.completed.connect(lambda model, glb: completed.append((model, glb)))
    worker.warning.connect(warnings.append)

    worker.run()

    assert completed == [(str(output), "")]
    assert warnings and "3D preview unavailable" in warnings[0]
    assert "renderer failed" in warnings[0]


def test_glb_export_preserves_memorymap_z_up_coordinates(tmp_path):
    from trimesh import Scene, load
    from trimesh.creation import box

    from memorymap_pipeline.desktop.preview import export_preview_glb

    mesh = box(extents=[2.0, 4.0, 6.0])
    output = export_preview_glb({"base": mesh}, tmp_path / "z-up.glb")
    loaded = load(output, force="scene")

    assert isinstance(loaded, Scene)
    assert loaded.extents.tolist() == pytest.approx([2.0, 4.0, 6.0])


def test_window_result_loads_preview_and_selects_tab(tmp_path):
    pytest.importorskip("PySide6")
    from memorymap_pipeline.desktop.window import MemoryMapWindow

    model = tmp_path / "memorymap.3mf"
    preview = tmp_path / "memorymap-preview.glb"
    model.write_bytes(b"3mf")
    preview.write_bytes(b"glTF")
    loaded = []
    selected = []

    class PreviewView:
        def load(self, url):
            loaded.append(url.toString())

        def setHtml(self, _html):
            raise AssertionError("Successful preview should be loaded, not replaced")

    window = SimpleNamespace(
        result_path=None,
        preview_path=None,
        save=SimpleNamespace(setEnabled=lambda enabled: None),
        progress=SimpleNamespace(setValue=lambda value: None),
        preview_view=PreviewView(),
        view_tabs=SimpleNamespace(setCurrentIndex=selected.append),
        preview_tab_index=1,
        add_warning=lambda message: None,
    )

    MemoryMapWindow.set_result(window, str(model), str(preview))

    assert window.result_path == model
    assert window.preview_path == preview
    assert loaded and loaded[0].startswith(viewer_directory().as_uri())
    assert selected == [1]
