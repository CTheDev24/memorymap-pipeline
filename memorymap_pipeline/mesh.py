from __future__ import annotations

from pathlib import Path

import numpy as np
from trimesh import Trimesh
from trimesh.creation import extrude_polygon


def build_route_mesh(points: np.ndarray, route_width_mm: float, height_mm: float) -> Trimesh:
    if len(points) < 2:
        raise ValueError("At least two points are required to form a route")

    segments = []
    for start, end in zip(points[:-1], points[1:]):
        segments.append((start, end))

    verts = []
    faces = []
    for index, (start, end) in enumerate(segments, start=1):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = np.hypot(dx, dy)
        if length == 0:
            continue

        nx = -dy / length
        ny = dx / length
        half_width = route_width_mm / 2.0

        p1 = (start[0] + nx * half_width, start[1] + ny * half_width, 0.0)
        p2 = (start[0] - nx * half_width, start[1] - ny * half_width, 0.0)
        p3 = (end[0] + nx * half_width, end[1] + ny * half_width, 0.0)
        p4 = (end[0] - nx * half_width, end[1] - ny * half_width, 0.0)

        top_points = [p1, p2, p3, p4]
        for point in top_points:
            verts.append(point)

        base_index = len(verts) - 4
        faces.extend([
            [base_index + 0, base_index + 1, base_index + 2],
            [base_index + 1, base_index + 3, base_index + 2],
        ])

        top_index = len(verts)
        verts.extend([
            (p1[0], p1[1], height_mm),
            (p2[0], p2[1], height_mm),
            (p3[0], p3[1], height_mm),
            (p4[0], p4[1], height_mm),
        ])
        faces.extend([
            [top_index + 0, top_index + 1, top_index + 2],
            [top_index + 1, top_index + 3, top_index + 2],
        ])

    if len(verts) == 0:
        raise ValueError("Could not create route mesh")

    return Trimesh(vertices=np.array(verts, dtype=float), faces=np.array(faces, dtype=int))


def build_base_plate(width_mm: float, height_mm: float, thickness_mm: float = 2.0) -> Trimesh:
    min_x = 0.0
    max_x = float(width_mm)
    min_y = 0.0
    max_y = float(height_mm)

    verts = [
        [min_x, min_y, -thickness_mm],
        [max_x, min_y, -thickness_mm],
        [max_x, max_y, -thickness_mm],
        [min_x, max_y, -thickness_mm],
        [min_x, min_y, 0.0],
        [max_x, min_y, 0.0],
        [max_x, max_y, 0.0],
        [min_x, max_y, 0.0],
    ]
    faces = [
        [0, 1, 2],
        [0, 2, 3],
        [4, 7, 5],
        [4, 6, 7],
        [0, 4, 5],
        [0, 5, 1],
        [1, 5, 6],
        [1, 6, 2],
        [2, 6, 7],
        [2, 7, 3],
        [3, 7, 4],
        [3, 4, 0],
    ]
    return Trimesh(vertices=np.array(verts, dtype=float), faces=np.array(faces, dtype=int))


def route_mesh_from_polygon(polygon, height_mm: float, z_offset: float = 0.0) -> Trimesh:
    """Extrude a Shapely polygon to a 3D Trimesh of given height (mm).

    The polygon bottom will be at z=z_offset and top at z=z_offset + height_mm.
    """
    mesh = extrude_polygon(polygon, height_mm)
    mesh.apply_translation((0.0, 0.0, z_offset))
    return mesh


def export_3mf(output_path: str | Path, base_mesh: Trimesh | None, route_mesh: Trimesh) -> None:
    from trimesh.exchange.export import export_mesh

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meshes = []

    if base_mesh is not None:
        try:
            base_mesh.metadata = base_mesh.metadata or {}
        except Exception:
            base_mesh.metadata = {}
        base_mesh.metadata["name"] = "Base_White"
        meshes.append(base_mesh)

    try:
        route_mesh.metadata = route_mesh.metadata or {}
    except Exception:
        route_mesh.metadata = {}
    route_mesh.metadata["name"] = "Route_Accent"
    meshes.append(route_mesh)

    export_mesh(
        meshes,
        output_path,
        file_type="3mf",
    )
