from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping

import numpy as np
from shapely.geometry import Polygon
from shapely.strtree import STRtree
from trimesh import Trimesh


@dataclass(frozen=True)
class PrintabilityIssue:
    code: str
    layer: str
    message: str


@dataclass(frozen=True)
class PrintabilityReport:
    issues: tuple[PrintabilityIssue, ...]

    @property
    def printable(self) -> bool:
        return not self.issues

    def raise_for_errors(self) -> None:
        if self.printable:
            return
        details = "; ".join(issue.message for issue in self.issues)
        raise ValueError(f"3MF printability validation failed: {details}")


@dataclass(frozen=True)
class _Component:
    layer: str
    index: int
    face_indices: np.ndarray

    @property
    def key(self) -> tuple[str, int]:
        return self.layer, self.index


@dataclass(frozen=True)
class _SurfaceTriangle:
    component: tuple[str, int]
    vertices: np.ndarray


def _mesh_components(layer: str, mesh: Trimesh) -> tuple[list[_Component], np.ndarray]:
    """Return vertex-connected face components without copying large meshes."""
    parent = np.arange(len(mesh.vertices), dtype=np.int64)

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = int(parent[value])
        return value

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    for first, second, third in np.asarray(mesh.faces, dtype=np.int64):
        union(int(first), int(second))
        union(int(second), int(third))

    groups: dict[int, list[int]] = defaultdict(list)
    for face_index, face in enumerate(mesh.faces):
        groups[find(int(face[0]))].append(face_index)

    components: list[_Component] = []
    face_component = np.empty(len(mesh.faces), dtype=np.int64)
    for component_index, face_indices in enumerate(groups.values()):
        indices = np.asarray(face_indices, dtype=np.int64)
        face_component[indices] = component_index
        components.append(_Component(layer, component_index, indices))
    return components, face_component


def _triangle_height(triangle: np.ndarray, x: float, y: float) -> float | None:
    first, second, third = triangle
    denominator = (
        (second[1] - third[1]) * (first[0] - third[0])
        + (third[0] - second[0]) * (first[1] - third[1])
    )
    if abs(denominator) <= 1e-12:
        return None
    first_weight = (
        (second[1] - third[1]) * (x - third[0])
        + (third[0] - second[0]) * (y - third[1])
    ) / denominator
    second_weight = (
        (third[1] - first[1]) * (x - third[0])
        + (first[0] - third[0]) * (y - third[1])
    ) / denominator
    third_weight = 1.0 - first_weight - second_weight
    if min(first_weight, second_weight, third_weight) < -1e-7:
        return None
    return float(
        first_weight * first[2]
        + second_weight * second[2]
        + third_weight * third[2]
    )


def _floating_components(
    active: Mapping[str, Trimesh],
    component_lookup: Mapping[tuple[str, int], _Component],
    face_components: Mapping[str, np.ndarray],
    *,
    contact_tolerance_mm: float,
    maximum_overlap_mm: float,
    maximum_samples_per_component: int,
) -> dict[str, list[_Component]]:
    """Return shells without a geometric support path to the base."""
    surface_polygons: list[Polygon] = []
    surface_triangles: list[_SurfaceTriangle] = []
    for layer, mesh in active.items():
        triangles = mesh.triangles
        normals = np.cross(
            triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        )
        for face_index in np.flatnonzero(normals[:, 2] > 1e-9):
            triangle = triangles[face_index]
            polygon = Polygon(triangle[:, :2])
            if polygon.area <= 1e-10:
                continue
            component = (layer, int(face_components[layer][face_index]))
            surface_polygons.append(polygon)
            surface_triangles.append(_SurfaceTriangle(component, triangle))

    if not surface_polygons:
        return {
            component.layer: [component]
            for component in component_lookup.values()
            if component.layer != "base"
        }

    tree = STRtree(surface_polygons)
    geometry_indices = {
        id(geometry): index for index, geometry in enumerate(surface_polygons)
    }
    support_graph: dict[tuple[str, int], set[tuple[str, int]]] = defaultdict(set)

    for key, component in component_lookup.items():
        if component.layer == "base":
            continue
        mesh = active[component.layer]
        triangles = mesh.triangles[component.face_indices]
        normals = np.cross(
            triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        )
        bottom = triangles[normals[:, 2] < -1e-9]
        if not len(bottom):
            bottom = triangles
        if len(bottom) > maximum_samples_per_component:
            sample_indices = np.linspace(
                0, len(bottom) - 1, maximum_samples_per_component, dtype=int
            )
            bottom = bottom[sample_indices]

        for bottom_triangle in bottom:
            bottom_polygon = Polygon(bottom_triangle[:, :2])
            if bottom_polygon.area <= 1e-10:
                continue
            candidates = tree.query(bottom_polygon)
            for candidate in candidates:
                if isinstance(candidate, (int, np.integer)):
                    index = int(candidate)
                else:  # Shapely 1.x compatibility
                    index = geometry_indices[id(candidate)]
                surface = surface_triangles[index]
                if surface.component == key:
                    continue
                overlap_region = bottom_polygon.intersection(surface_polygons[index])
                if overlap_region.area <= 1e-8:
                    continue
                sample = overlap_region.representative_point()
                support_z = _triangle_height(surface.vertices, sample.x, sample.y)
                bottom_z = _triangle_height(bottom_triangle, sample.x, sample.y)
                if support_z is None or bottom_z is None:
                    continue
                overlap = support_z - bottom_z
                if -contact_tolerance_mm <= overlap <= maximum_overlap_mm:
                    support_graph[key].add(surface.component)

    connected = {key for key in component_lookup if key[0] == "base"}
    changed = True
    while changed:
        changed = False
        for key, supports in support_graph.items():
            if key not in connected and supports.intersection(connected):
                connected.add(key)
                changed = True

    floating_by_layer: dict[str, list[_Component]] = defaultdict(list)
    for key, component in component_lookup.items():
        if component.layer != "base" and key not in connected:
            floating_by_layer[component.layer].append(component)
    return dict(floating_by_layer)


def remove_small_floating_components(
    meshes: Mapping[str, Trimesh | None],
    layer: str,
    *,
    maximum_faces: int = 12,
    contact_tolerance_mm: float = 0.03,
    maximum_overlap_mm: float = 5.0,
    maximum_samples_per_component: int = 96,
) -> tuple[Trimesh | None, int]:
    """Drop only tiny shells proven to have no support path to the base."""
    active = {name: mesh for name, mesh in meshes.items() if mesh is not None}
    target = active.get(layer)
    if target is None or "base" not in active:
        return target, 0

    component_lookup: dict[tuple[str, int], _Component] = {}
    face_components: dict[str, np.ndarray] = {}
    for name, mesh in active.items():
        components, face_component = _mesh_components(name, mesh)
        face_components[name] = face_component
        component_lookup.update((component.key, component) for component in components)

    floating = _floating_components(
        active,
        component_lookup,
        face_components,
        contact_tolerance_mm=contact_tolerance_mm,
        maximum_overlap_mm=maximum_overlap_mm,
        maximum_samples_per_component=maximum_samples_per_component,
    ).get(layer, [])
    removable = [
        component
        for component in floating
        if len(component.face_indices) <= maximum_faces
    ]
    if not removable:
        return target, 0

    keep = np.ones(len(target.faces), dtype=bool)
    for component in removable:
        keep[component.face_indices] = False
    cleaned = Trimesh(
        vertices=np.asarray(target.vertices).copy(),
        faces=np.asarray(target.faces)[keep],
        process=False,
    )
    cleaned.remove_unreferenced_vertices()
    cleaned.fix_normals(multibody=True)
    return cleaned, len(removable)


def audit_printability(
    meshes: Mapping[str, Trimesh | None],
    *,
    contact_tolerance_mm: float = 0.03,
    maximum_overlap_mm: float = 5.0,
    maximum_samples_per_component: int = 96,
) -> PrintabilityReport:
    """Check manifold topology and ensure every shell has a support path to the base.

    Multipart colors remain separate 3MF objects, so support is determined geometrically:
    a shell may contact or overlap the base, or may be supported by another shell which
    ultimately contacts the base. Cycles of mutually touching floating shells do not pass.
    """
    active = {name: mesh for name, mesh in meshes.items() if mesh is not None}
    issues: list[PrintabilityIssue] = []
    component_lookup: dict[tuple[str, int], _Component] = {}
    face_components: dict[str, np.ndarray] = {}
    invalid_coordinates = False

    for layer, mesh in active.items():
        if not np.isfinite(mesh.vertices).all():
            invalid_coordinates = True
            issues.append(
                PrintabilityIssue(
                    "invalid_coordinates",
                    layer,
                    f"{layer} contains invalid coordinates",
                )
            )
            continue
        edge_counts = np.bincount(mesh.edges_unique_inverse)
        boundary_edges = int(np.count_nonzero(edge_counts == 1))
        overused_edges = int(np.count_nonzero(edge_counts > 2))
        if boundary_edges or overused_edges:
            issues.append(
                PrintabilityIssue(
                    "non_manifold",
                    layer,
                    f"{layer} is non-manifold ({boundary_edges} open and "
                    f"{overused_edges} over-connected edges)",
                )
            )
        if not mesh.is_winding_consistent:
            issues.append(
                PrintabilityIssue(
                    "inconsistent_winding",
                    layer,
                    f"{layer} has inconsistent face winding",
                )
            )
        if mesh.is_watertight and mesh.volume <= 0.0:
            issues.append(
                PrintabilityIssue(
                    "non_positive_volume",
                    layer,
                    f"{layer} does not define a positive outward-facing volume",
                )
            )
        components, face_component = _mesh_components(layer, mesh)
        face_components[layer] = face_component
        component_lookup.update((component.key, component) for component in components)

    if invalid_coordinates or "base" not in active:
        return PrintabilityReport(tuple(issues))

    floating_by_layer = _floating_components(
        active,
        component_lookup,
        face_components,
        contact_tolerance_mm=contact_tolerance_mm,
        maximum_overlap_mm=maximum_overlap_mm,
        maximum_samples_per_component=maximum_samples_per_component,
    )
    if not any(
        np.any(
            np.cross(
                mesh.triangles[:, 1] - mesh.triangles[:, 0],
                mesh.triangles[:, 2] - mesh.triangles[:, 0],
            )[:, 2]
            > 1e-9
        )
        for mesh in active.values()
    ):
        issues.append(
            PrintabilityIssue("missing_support", "base", "base has no upward surface")
        )
        return PrintabilityReport(tuple(issues))
    for layer, components in floating_by_layer.items():
        largest = max(len(component.face_indices) for component in components)
        issues.append(
            PrintabilityIssue(
                "floating_components",
                layer,
                f"{layer} contains {len(components)} floating shell(s); "
                f"the largest has {largest} faces",
            )
        )

    return PrintabilityReport(tuple(issues))


__all__ = [
    "PrintabilityIssue",
    "PrintabilityReport",
    "audit_printability",
    "remove_small_floating_components",
]
