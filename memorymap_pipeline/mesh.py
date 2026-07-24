from __future__ import annotations

import re
import zipfile
from pathlib import Path

import numpy as np
from shapely import contains_xy
from shapely.geometry.base import BaseGeometry
from trimesh import Trimesh
from trimesh.creation import extrude_polygon

DEFAULT_FEATURE_EMBED_DEPTH_MM = 0.2

MESH_COLORS = {
    "base": np.array([255, 255, 255, 255], dtype=np.uint8),
    "route": np.array([255, 102, 51, 255], dtype=np.uint8),
    "roads": np.array([0, 0, 0, 255], dtype=np.uint8),
    "buildings": np.array([128, 128, 128, 255], dtype=np.uint8),
    "water": np.array([128, 128, 128, 255], dtype=np.uint8),
}

MESH_MATERIALS = {
    "Base_White": ("White", "#FFFFFFFF"),
    "Route_Accent": ("Orange", "#FF6633FF"),
    "Roads_Black": ("Black", "#000000FF"),
    "Buildings_Verification": ("Gray", "#808080FF"),
    "Water_Gray": ("Gray Water", "#808080FF"),
}

def embedded_feature_dimensions(
    visible_height_mm: float,
    base_thickness_mm: float,
    embed_depth_mm: float = DEFAULT_FEATURE_EMBED_DEPTH_MM,
) -> tuple[float, float, float]:
    """Return extrusion height, bottom Z, and effective embed for a raised feature.

    Feature heights are user-facing visible heights above the base top plane (Z=0).
    The mesh extends downward into the base by at most ``embed_depth_mm`` so separate
    3MF objects overlap reliably without changing the requested visible height.
    """
    if visible_height_mm <= 0:
        raise ValueError("Visible feature height must be positive")
    if base_thickness_mm < 0 or embed_depth_mm < 0:
        raise ValueError("Base thickness and feature embed depth cannot be negative")
    effective_embed = min(base_thickness_mm, embed_depth_mm)
    return visible_height_mm + effective_embed, -effective_embed, effective_embed


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
        [0, 2, 1],
        [0, 3, 2],
        [4, 5, 6],
        [4, 6, 7],
        [0, 1, 5],
        [0, 5, 4],
        [1, 2, 6],
        [1, 6, 5],
        [2, 3, 7],
        [2, 7, 6],
        [3, 0, 4],
        [3, 4, 7],
    ]
    return Trimesh(vertices=np.array(verts, dtype=float), faces=np.array(faces, dtype=int))


def route_mesh_from_polygon(polygon, height_mm: float, z_offset: float = 0.0) -> Trimesh:
    """Extrude a Shapely polygon to a 3D Trimesh of given height (mm).

    The polygon bottom will be at z=z_offset and top at z=z_offset + height_mm.
    """
    mesh = extrude_polygon(polygon, height_mm)
    mesh.apply_translation((0.0, 0.0, z_offset))
    valid_faces = np.isfinite(mesh.area_faces) & (mesh.area_faces > 1e-12)
    if not np.all(valid_faces):
        mesh.update_faces(valid_faces)
        mesh.remove_unreferenced_vertices()
    if len(mesh.faces) == 0:
        raise ValueError("Polygon extrusion produced no valid faces")
    if not mesh.is_watertight:
        raise ValueError("Polygon extrusion produced a non-watertight mesh")
    return mesh


def refine_mesh_edges(
    mesh: Trimesh,
    maximum_edge_mm: float,
    region: BaseGeometry | None = None,
    *,
    maximum_iterations: int = 16,
    allow_partial: bool = False,
) -> Trimesh:
    """Conformingly subdivide selected mesh edges to a length limit.

    Each selected shared edge is split in every incident face during the same pass.
    This preserves watertight topology and avoids the T-junctions produced by
    independently refining individual triangles. Midpoints retain the XY footprint.
    Without ``region`` every edge is eligible; otherwise refinement stays local.
    """
    if maximum_edge_mm <= 0.0:
        raise ValueError("Maximum edge length must be positive")
    if maximum_iterations <= 0:
        raise ValueError("Maximum refinement iterations must be positive")

    def result_mesh(*, incomplete: bool = False) -> Trimesh:
        result = Trimesh(vertices=vertices, faces=faces, process=False)
        result.remove_unreferenced_vertices()
        result.metadata.update(mesh.metadata or {})
        if incomplete:
            result.metadata["edge_refinement_incomplete"] = True
        return result

    vertices = np.asarray(mesh.vertices, dtype=float).copy()
    faces = np.asarray(mesh.faces, dtype=np.int64).copy()
    for _iteration in range(maximum_iterations):
        edge_pairs = np.stack(
            (faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]), axis=1
        )
        canonical = np.sort(edge_pairs, axis=2)
        unique_edges, inverse = np.unique(
            canonical.reshape(-1, 2), axis=0, return_inverse=True
        )
        edge_lengths = np.linalg.norm(
            vertices[unique_edges[:, 1]] - vertices[unique_edges[:, 0]], axis=1
        )
        split_unique = edge_lengths > maximum_edge_mm + 1e-9
        if region is not None:
            buffered_region = region.buffer(1e-7)
            face_vertices = vertices[faces]
            centroids = face_vertices.mean(axis=1)
            face_in_region = contains_xy(
                buffered_region, centroids[:, 0], centroids[:, 1]
            ) | np.any(
                contains_xy(
                    buffered_region,
                    face_vertices[:, :, 0].ravel(),
                    face_vertices[:, :, 1].ravel(),
                ).reshape(-1, 3),
                axis=1,
            )
            edge_in_region = np.zeros(len(unique_edges), dtype=bool)
            np.logical_or.at(
                edge_in_region,
                inverse,
                np.repeat(face_in_region, 3),
            )
            split_unique &= edge_in_region
        if not np.any(split_unique):
            return result_mesh()

        midpoint_indices = np.full(len(unique_edges), -1, dtype=np.int64)
        selected_edges = unique_edges[split_unique]
        midpoint_indices[split_unique] = np.arange(
            len(vertices), len(vertices) + len(selected_edges), dtype=np.int64
        )
        vertices = np.vstack((vertices, vertices[selected_edges].mean(axis=1)))
        face_edge_indices = inverse.reshape(-1, 3)
        split = split_unique[face_edge_indices]
        midpoints = midpoint_indices[face_edge_indices]
        refined_faces: list[list[int]] = []
        for (a, b, c), flags, (ab, bc, ca) in zip(
            faces, split, midpoints, strict=True
        ):
            mask = int(flags[0]) | (int(flags[1]) << 1) | (int(flags[2]) << 2)
            if mask == 0:
                refined_faces.append([a, b, c])
            elif mask == 1:
                refined_faces.extend(([a, ab, c], [ab, b, c]))
            elif mask == 2:
                refined_faces.extend(([b, bc, a], [bc, c, a]))
            elif mask == 4:
                refined_faces.extend(([c, ca, b], [ca, a, b]))
            elif mask == 3:
                refined_faces.extend(([b, bc, ab], [a, ab, c], [ab, bc, c]))
            elif mask == 6:
                refined_faces.extend(([c, ca, bc], [b, bc, a], [bc, ca, a]))
            elif mask == 5:
                refined_faces.extend(([a, ab, ca], [c, ca, b], [ca, ab, b]))
            else:
                refined_faces.extend(
                    ([a, ab, ca], [ab, b, bc], [ca, bc, c], [ab, bc, ca])
                )
        faces = np.asarray(refined_faces, dtype=np.int64)

    if allow_partial:
        return result_mesh(incomplete=True)
    raise ValueError("Mesh edge refinement exceeded the iteration limit")


def center_meshes_to_base(meshes: list[Trimesh], width_mm: float, height_mm: float) -> None:
    """Center a list of meshes together within the base dimensions in XY."""
    if not meshes:
        return

    xs_min = min(mesh.bounds[0, 0] for mesh in meshes)
    ys_min = min(mesh.bounds[0, 1] for mesh in meshes)
    xs_max = max(mesh.bounds[1, 0] for mesh in meshes)
    ys_max = max(mesh.bounds[1, 1] for mesh in meshes)

    combined_width = xs_max - xs_min
    combined_height = ys_max - ys_min
    offset_x = (width_mm - combined_width) / 2.0 - xs_min
    offset_y = (height_mm - combined_height) / 2.0 - ys_min

    for mesh in meshes:
        mesh.apply_translation((offset_x, offset_y, 0.0))


def _apply_3mf_materials(output_path: Path) -> None:
    """Inject slicer-visible materials without reserializing the model XML.

    Some slicers reject otherwise valid 3MF files when the core namespace is rewritten
    with generated prefixes. Preserve the exporter document and make only targeted text
    insertions instead.
    """
    with zipfile.ZipFile(output_path, "r") as source:
        entries = [(info, source.read(info.filename)) for info in source.infolist()]

    model_index = next(
        (index for index, (info, _data) in enumerate(entries) if info.filename.lower().endswith(".model")),
        None,
    )
    if model_index is None:
        raise ValueError("Exported 3MF archive does not contain a model document")

    model_info, model_data = entries[model_index]
    model_text = model_data.decode("utf-8")
    resource_match = re.search(r"<(?:[A-Za-z_][\w.-]*:)?resources\b[^>]*>", model_text)
    if resource_match is None:
        raise ValueError("Exported 3MF model does not contain resources")

    used_ids = {int(value) for value in re.findall(r'\bid="(\d+)"', model_text)}
    material_id = max(used_ids, default=0) + 1
    material_xml = [f'<basematerials id="{material_id}">']
    material_indices: dict[str, int] = {}
    for index, (object_name, (material_name, display_color)) in enumerate(MESH_MATERIALS.items()):
        material_xml.append(
            f'<base name="{material_name}" displaycolor="{display_color}" />'
        )
        material_indices[object_name] = index
    material_xml.append("</basematerials>")
    insertion_point = resource_match.end()
    model_text = (
        model_text[:insertion_point]
        + "".join(material_xml)
        + model_text[insertion_point:]
    )

    component_ids: list[int] = []
    for object_name, material_index in material_indices.items():
        object_pattern = re.compile(
            rf'(<(?:[A-Za-z_][\w.-]*:)?object\b(?=[^>]*\bname="{re.escape(object_name)}")[^>]*)(>)'
        )
        object_match = object_pattern.search(model_text)
        if object_match is not None:
            id_match = re.search(r'\bid="(\d+)"', object_match.group(1))
            if id_match is None:
                raise ValueError(f"Exported 3MF object {object_name} has no resource id")
            component_ids.append(int(id_match.group(1)))
        model_text, replacements = object_pattern.subn(
            rf'\1 pid="{material_id}" pindex="{material_index}"\2',
            model_text,
            count=1,
        )
        if replacements > 1:
            raise ValueError(f"Exported 3MF model contains duplicate object {object_name}")

    if not component_ids:
        raise ValueError("Exported 3MF model contains no printable components")

    assembly_id = material_id + 1
    components_xml = "".join(
        f'<component objectid="{component_id}" />' for component_id in component_ids
    )
    assembly_xml = (
        f'<object id="{assembly_id}" name="MemoryMap" type="model">'
        f'<components>{components_xml}</components></object>'
    )
    resources_end = re.search(r"</(?:[A-Za-z_][\w.-]*:)?resources>", model_text)
    if resources_end is None:
        raise ValueError("Exported 3MF model has no resources closing tag")
    model_text = (
        model_text[: resources_end.start()]
        + assembly_xml
        + model_text[resources_end.start() :]
    )

    build_pattern = re.compile(
        r"(<(?:[A-Za-z_][\w.-]*:)?build\b[^>]*>).*?(</(?:[A-Za-z_][\w.-]*:)?build>)",
        re.DOTALL,
    )
    model_text, build_replacements = build_pattern.subn(
        rf'\1<item objectid="{assembly_id}" />\2', model_text, count=1
    )
    if build_replacements != 1:
        raise ValueError("Exported 3MF model does not contain exactly one build section")

    entries[model_index] = (
        model_info,
        model_text.encode("utf-8"),
    )
    temporary_path = output_path.with_name(f"{output_path.name}.materials.tmp")
    try:
        with zipfile.ZipFile(temporary_path, "w") as destination:
            for info, data in entries:
                destination.writestr(info, data)
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def export_3mf(
    output_path: str | Path,
    base_mesh: Trimesh | None,
    route_mesh: Trimesh | None,
    roads_mesh: Trimesh | None = None,
    buildings_mesh: Trimesh | None = None,
    water_mesh: Trimesh | None = None,
) -> None:
    from trimesh.exchange.export import export_mesh

    from .printability import audit_printability

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meshes = []

    if base_mesh is not None:
        try:
            base_mesh.metadata = base_mesh.metadata or {}
        except Exception:
            base_mesh.metadata = {}
        base_mesh.metadata["name"] = "Base_White"
        base_mesh.visual.face_colors = MESH_COLORS["base"]
        meshes.append(base_mesh)

    if route_mesh is not None:
        try:
            route_mesh.metadata = route_mesh.metadata or {}
        except Exception:
            route_mesh.metadata = {}
        route_mesh.metadata["name"] = "Route_Accent"
        route_mesh.visual.face_colors = MESH_COLORS["route"]
        meshes.append(route_mesh)

    if roads_mesh is not None:
        try:
            roads_mesh.metadata = roads_mesh.metadata or {}
        except Exception:
            roads_mesh.metadata = {}
        roads_mesh.metadata["name"] = "Roads_Black"
        roads_mesh.visual.face_colors = MESH_COLORS["roads"]
        meshes.append(roads_mesh)

    if buildings_mesh is not None:
        try:
            buildings_mesh.metadata = buildings_mesh.metadata or {}
        except Exception:
            buildings_mesh.metadata = {}
        buildings_mesh.metadata["name"] = "Buildings_Verification"
        buildings_mesh.visual.face_colors = MESH_COLORS["buildings"]
        meshes.append(buildings_mesh)

    if water_mesh is not None:
        try:
            water_mesh.metadata = water_mesh.metadata or {}
        except Exception:
            water_mesh.metadata = {}
        water_mesh.metadata["name"] = "Water_Gray"
        water_mesh.visual.face_colors = MESH_COLORS["water"]
        meshes.append(water_mesh)

    audit_printability(
        {
            "base": base_mesh,
            "route": route_mesh,
            "roads": roads_mesh,
            "buildings": buildings_mesh,
            "water": water_mesh,
        }
    ).raise_for_errors()

    export_mesh(
        meshes,
        output_path,
        file_type="3mf",
    )
    _apply_3mf_materials(output_path)
