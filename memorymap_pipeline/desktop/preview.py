"""Build a lightweight, colored GLB scene for the desktop 3D preview.

The preview is deliberately exported from copies of the production meshes.  A
viewer may therefore simplify its copy without changing the geometry, colors,
or metadata that are subsequently written to the printable 3MF.
"""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Mapping
from uuid import uuid4

import numpy as np
from trimesh import Scene, Trimesh


DEFAULT_MAX_PREVIEW_FACES = 750_000


def _display_name(layer_name: str, mesh: Trimesh) -> str:
    metadata_name = (mesh.metadata or {}).get("name")
    if isinstance(metadata_name, str) and metadata_name.strip():
        return metadata_name.strip()
    return str(layer_name)


def _unique_name(name: str, used_names: set[str]) -> str:
    if name not in used_names:
        used_names.add(name)
        return name
    suffix = 2
    while f"{name}_{suffix}" in used_names:
        suffix += 1
    unique = f"{name}_{suffix}"
    used_names.add(unique)
    return unique


def _uniform_color(mesh: Trimesh) -> np.ndarray | None:
    """Return the layer RGBA color when it is uniform, without altering *mesh*."""
    try:
        colors = np.asarray(mesh.visual.face_colors, dtype=np.uint8)
    except (AttributeError, TypeError, ValueError):
        return None
    if colors.ndim != 2 or len(colors) == 0:
        return None
    unique = np.unique(colors, axis=0)
    return unique[0].copy() if len(unique) == 1 else None


def _vertex_colors_without_scipy(mesh: Trimesh) -> np.ndarray:
    """Convert preview colors to per-vertex RGBA using only NumPy."""
    try:
        if mesh.visual.kind == "vertex":
            vertex_colors = np.asarray(mesh.visual.vertex_colors, dtype=np.uint8)
            if vertex_colors.shape == (len(mesh.vertices), 4):
                return vertex_colors.copy()
        face_colors = np.asarray(mesh.visual.face_colors, dtype=np.uint8)
    except (AttributeError, TypeError, ValueError):
        face_colors = np.empty((0, 4), dtype=np.uint8)

    if face_colors.shape != (len(mesh.faces), 4):
        return np.tile(np.array([102, 102, 102, 255], dtype=np.uint8), (len(mesh.vertices), 1))

    totals = np.zeros((len(mesh.vertices), 4), dtype=np.float64)
    counts = np.zeros(len(mesh.vertices), dtype=np.int64)
    vertex_indices = np.asarray(mesh.faces).reshape(-1)
    np.add.at(totals, vertex_indices, np.repeat(face_colors, 3, axis=0))
    np.add.at(counts, vertex_indices, 1)
    counts[counts == 0] = 1
    return np.rint(totals / counts[:, None]).astype(np.uint8)


def _preview_mesh(mesh: Trimesh, target_faces: int | None) -> Trimesh:
    # Trimesh.copy() may ask scipy to convert face colors to vertex colors.
    # Rebuilding the preview copy explicitly keeps scipy optional and also
    # makes the ownership boundary between print and preview geometry clear.
    preview = Trimesh(
        vertices=np.asarray(mesh.vertices).copy(),
        faces=np.asarray(mesh.faces).copy(),
        metadata=deepcopy(mesh.metadata or {}),
        process=False,
    )
    preview.visual.vertex_colors = _vertex_colors_without_scipy(mesh)

    if target_faces is None or len(preview.faces) <= target_faces:
        return preview

    # Quadric decimation is an optional trimesh capability.  Only use it for
    # the uniformly colored layer meshes emitted by MemoryMap, since there is
    # no safe correspondence between old and new faces for a multicolor mesh.
    color = _uniform_color(preview)
    if color is None:
        return preview
    try:
        simplified = preview.simplify_quadric_decimation(face_count=target_faces)
    except (ImportError, ModuleNotFoundError, RuntimeError, TypeError, ValueError):
        return preview
    if (
        not isinstance(simplified, Trimesh)
        or simplified.is_empty
        or len(simplified.faces) >= len(preview.faces)
        or not np.isfinite(simplified.vertices).all()
    ):
        return preview

    simplified.metadata = deepcopy(preview.metadata)
    simplified.metadata["preview_source_faces"] = int(len(preview.faces))
    simplified.metadata["preview_simplified"] = True
    simplified.visual.vertex_colors = np.tile(color, (len(simplified.vertices), 1))
    return simplified


def _scene_from_meshes(
    meshes: Mapping[str, Trimesh],
    *,
    max_faces: int,
) -> Scene:
    if max_faces <= 0:
        raise ValueError("max_faces must be greater than zero")
    if not meshes:
        raise ValueError("At least one mesh is required for a 3D preview")

    active: list[tuple[str, Trimesh]] = []
    for layer_name, mesh in meshes.items():
        if not isinstance(mesh, Trimesh):
            raise TypeError(f"Preview layer {layer_name!r} is not a trimesh.Trimesh")
        if not mesh.is_empty and len(mesh.faces):
            active.append((str(layer_name), mesh))
    if not active:
        raise ValueError("At least one non-empty mesh is required for a 3D preview")

    total_faces = sum(len(mesh.faces) for _, mesh in active)
    scene = Scene()
    # Production meshes and the desktop viewer both use Z-up coordinates.
    # GLB export preserves these coordinates; no hidden root rotation is added.
    scene.metadata["up_axis"] = "Z"
    scene.metadata["units"] = "millimeters"
    used_names: set[str] = set()
    for layer_name, mesh in active:
        target_faces = None
        if total_faces > max_faces:
            target_faces = max(4, round(max_faces * len(mesh.faces) / total_faces))
        name = _unique_name(_display_name(layer_name, mesh), used_names)
        preview = _preview_mesh(mesh, target_faces)
        preview.metadata["name"] = name
        preview.metadata["preview_layer"] = layer_name
        scene.add_geometry(preview, node_name=name, geom_name=name)
    return scene


def _export_scene_bytes(scene: Scene) -> bytes:
    payload = scene.export(file_type="glb")
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        raise RuntimeError("Trimesh did not produce a valid binary GLB payload")
    return bytes(payload)


def export_preview_glb(
    meshes: Mapping[str, Trimesh],
    output_path: str | Path,
    *,
    max_faces: int = DEFAULT_MAX_PREVIEW_FACES,
) -> Path:
    """Export *meshes* as an atomically written, self-contained binary GLB.

    Mesh metadata names and visual colors are retained.  The input meshes are
    never modified.  When the combined scene exceeds ``max_faces``, uniformly
    colored preview copies are opportunistically simplified if trimesh's
    optional simplifier is installed; otherwise full-detail copies are used.
    """
    destination = Path(output_path)
    if destination.suffix.lower() != ".glb":
        raise ValueError("Preview output path must use the .glb extension")

    scene = _scene_from_meshes(meshes, max_faces=max_faces)
    payload = _export_scene_bytes(scene)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


__all__ = ["DEFAULT_MAX_PREVIEW_FACES", "export_preview_glb"]
