from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest
import trimesh
from trimesh.creation import box

from memorymap_pipeline.desktop.preview import export_preview_glb


def _colored_box(name: str, color: list[int]) -> trimesh.Trimesh:
    mesh = box(extents=[4.0, 3.0, 2.0])
    mesh.metadata["name"] = name
    mesh.visual.face_colors = np.asarray(color, dtype=np.uint8)
    return mesh


def test_preview_glb_preserves_names_colors_and_is_self_contained(tmp_path):
    base = _colored_box("Base_Bone", [214, 203, 171, 255])
    route = _colored_box("Route_Accent", [255, 102, 51, 255])
    route.apply_translation([6.0, 0.0, 1.0])

    output = export_preview_glb({"base": base, "route": route}, tmp_path / "preview.glb")

    assert output == tmp_path / "preview.glb"
    assert output.read_bytes()[:4] == b"glTF"
    loaded = trimesh.load(output, force="scene")
    assert isinstance(loaded, trimesh.Scene)
    assert set(loaded.geometry) == {"Base_Bone", "Route_Accent"}
    assert np.array_equal(
        np.asarray(loaded.geometry["Base_Bone"].visual.vertex_colors)[0],
        [214, 203, 171, 255],
    )
    assert np.array_equal(
        np.asarray(loaded.geometry["Route_Accent"].visual.vertex_colors)[0],
        [255, 102, 51, 255],
    )
    assert np.allclose(loaded.bounds, np.array([[-2.0, -1.5, -1.0], [8.0, 1.5, 2.0]]))


def test_preview_export_does_not_modify_source_meshes(tmp_path):
    mesh = _colored_box("Buildings_Verification", [128, 128, 128, 255])
    vertices = mesh.vertices.copy()
    faces = mesh.faces.copy()
    colors = np.asarray(mesh.visual.face_colors).copy()
    metadata = deepcopy(mesh.metadata)

    export_preview_glb({"buildings": mesh}, tmp_path / "preview.glb", max_faces=4)

    assert np.array_equal(mesh.vertices, vertices)
    assert np.array_equal(mesh.faces, faces)
    assert np.array_equal(np.asarray(mesh.visual.face_colors), colors)
    assert mesh.metadata.keys() == metadata.keys()
    for key, expected in metadata.items():
        actual = mesh.metadata[key]
        if isinstance(expected, np.ndarray):
            assert np.array_equal(actual, expected)
        else:
            assert actual == expected


def test_preview_uses_mapping_key_when_mesh_has_no_name(tmp_path):
    mesh = box()
    mesh.metadata.clear()

    output = export_preview_glb({"water": mesh}, tmp_path / "preview.glb")

    loaded = trimesh.load(output, force="scene")
    assert set(loaded.geometry) == {"water"}


@pytest.mark.parametrize(
    ("meshes", "error", "message"),
    [
        ({}, ValueError, "At least one mesh"),
        ({"empty": trimesh.Trimesh()}, ValueError, "non-empty"),
        ({"bad": object()}, TypeError, "not a trimesh"),
    ],
)
def test_preview_rejects_empty_or_invalid_meshes(tmp_path, meshes, error, message):
    with pytest.raises(error, match=message):
        export_preview_glb(meshes, tmp_path / "preview.glb")


def test_preview_validates_path_and_face_limit(tmp_path):
    mesh = box()
    with pytest.raises(ValueError, match=r"\.glb"):
        export_preview_glb({"base": mesh}, tmp_path / "preview.obj")
    with pytest.raises(ValueError, match="max_faces"):
        export_preview_glb({"base": mesh}, tmp_path / "preview.glb", max_faces=0)


def test_preview_write_is_atomic_when_export_fails(tmp_path, monkeypatch):
    destination = tmp_path / "preview.glb"
    destination.write_bytes(b"previous-preview")

    def fail_export(_scene):
        raise RuntimeError("synthetic exporter failure")

    monkeypatch.setattr("memorymap_pipeline.desktop.preview._export_scene_bytes", fail_export)
    with pytest.raises(RuntimeError, match="synthetic"):
        export_preview_glb({"base": box()}, destination)

    assert destination.read_bytes() == b"previous-preview"
    assert list(tmp_path.iterdir()) == [destination]
