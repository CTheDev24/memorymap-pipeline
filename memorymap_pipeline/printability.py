from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal, Mapping

import numpy as np
from shapely.geometry import Polygon
from shapely.strtree import STRtree
from trimesh import Trimesh


@dataclass(frozen=True)
class PrintabilityIssue:
    code: str
    layer: str
    message: str
    severity: Literal["warning", "error"] = "error"
    blocking: bool = False


@dataclass(frozen=True)
class PrintabilityProfile:
    """Printer-aware thresholds used by the production preflight audit."""

    nozzle_diameter_mm: float = 0.4
    layer_height_mm: float = 0.16
    minimum_xy_feature_mm: float = 0.8
    minimum_z_feature_mm: float = 0.32

    def __post_init__(self) -> None:
        values = (
            self.nozzle_diameter_mm,
            self.layer_height_mm,
            self.minimum_xy_feature_mm,
            self.minimum_z_feature_mm,
        )
        if any(value <= 0 for value in values):
            raise ValueError("Printability profile dimensions must be positive")


@dataclass(frozen=True)
class PrintabilityReport:
    issues: tuple[PrintabilityIssue, ...]

    @property
    def status(self) -> Literal["green", "yellow", "red"]:
        if any(issue.severity == "error" for issue in self.issues):
            return "red"
        if self.issues:
            return "yellow"
        return "green"

    @property
    def printable(self) -> bool:
        return not self.issues

    @property
    def exportable(self) -> bool:
        return not any(issue.blocking for issue in self.issues)

    @property
    def blocking_issues(self) -> tuple[PrintabilityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "exportable": self.exportable,
            "issues": [
                {
                    "code": issue.code,
                    "layer": issue.layer,
                    "message": issue.message,
                    "severity": issue.severity,
                    "blocking": issue.blocking,
                }
                for issue in self.issues
            ],
        }

    def summary(self) -> str:
        if not self.issues:
            return "GREEN - no printability issues detected"
        errors = sum(issue.severity == "error" for issue in self.issues)
        warnings = len(self.issues) - errors
        return f"{self.status.upper()} - {errors} error(s), {warnings} warning(s)"

    def raise_for_errors(self) -> None:
        if self.exportable:
            return
        details = "; ".join(issue.message for issue in self.blocking_issues)
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


def _component_xy_width(mesh: Trimesh, component: _Component) -> float:
    """Return a conservative oriented XY width for an isolated shell."""
    vertex_indices = np.unique(mesh.faces[component.face_indices].reshape(-1))
    points = np.asarray(mesh.vertices[vertex_indices, :2], dtype=float)
    if len(points) < 3:
        return 0.0
    centered = points - points.mean(axis=0)
    covariance = centered.T @ centered
    _, axes = np.linalg.eigh(covariance)
    extents = np.ptp(centered @ axes, axis=0)
    return float(np.min(extents))


def audit_printability(
    meshes: Mapping[str, Trimesh | None],
    *,
    profile: PrintabilityProfile | None = None,
    print_size_mm: tuple[float, float] | None = None,
    margin_mm: float | None = None,
    declared_feature_widths_mm: Mapping[str, float] | None = None,
    contact_tolerance_mm: float = 0.03,
    maximum_overlap_mm: float = 5.0,
    maximum_samples_per_component: int = 96,
) -> PrintabilityReport:
    """Check manifold topology and ensure every shell has a support path to the base.

    Multipart colors remain separate 3MF objects, so support is determined geometrically:
    a shell may contact or overlap the base, or may be supported by another shell which
    ultimately contacts the base. Cycles of mutually touching floating shells do not pass.
    """
    profile = profile or PrintabilityProfile()
    declared_feature_widths_mm = declared_feature_widths_mm or {}
    active = {name: mesh for name, mesh in meshes.items() if mesh is not None}
    issues: list[PrintabilityIssue] = []
    component_lookup: dict[tuple[str, int], _Component] = {}
    face_components: dict[str, np.ndarray] = {}
    invalid_coordinates = False

    if not active:
        return PrintabilityReport(
            (PrintabilityIssue("empty_model", "model", "No mesh geometry was generated", blocking=True),)
        )

    for layer, mesh in active.items():
        if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
            issues.append(
                PrintabilityIssue(
                    "empty_geometry", layer, f"{layer} contains no printable triangles", blocking=True
                )
            )
            continue
        if not np.isfinite(mesh.vertices).all():
            invalid_coordinates = True
            issues.append(
                PrintabilityIssue(
                    "invalid_coordinates",
                    layer,
                    f"{layer} contains invalid coordinates",
                    blocking=True,
                )
            )
            continue
        faces = np.asarray(mesh.faces, dtype=np.int64)
        repeated = np.any(
            (faces[:, 0] == faces[:, 1])
            | (faces[:, 1] == faces[:, 2])
            | (faces[:, 2] == faces[:, 0])
        )
        degenerate_count = int(np.count_nonzero(np.asarray(mesh.area_faces) <= 1e-10))
        if repeated or degenerate_count:
            issues.append(
                PrintabilityIssue(
                    "degenerate_faces",
                    layer,
                    f"{layer} contains {degenerate_count} zero-area face(s)",
                )
            )
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

        declared_width = declared_feature_widths_mm.get(layer)
        if declared_width is not None and declared_width < profile.minimum_xy_feature_mm:
            issues.append(
                PrintabilityIssue(
                    "thin_xy_feature",
                    layer,
                    f"{layer} is configured at {declared_width:.2f} mm; the "
                    f"{profile.nozzle_diameter_mm:.1f} mm nozzle profile recommends "
                    f"{profile.minimum_xy_feature_mm:.2f} mm",
                    severity="warning",
                )
            )

        if layer not in {"base", "route", "roads"}:
            thin_widths = [
                width
                for component in components
                if (width := _component_xy_width(mesh, component))
                < profile.minimum_xy_feature_mm - 1e-6
            ]
            if thin_widths:
                issues.append(
                    PrintabilityIssue(
                        "thin_xy_shell",
                        layer,
                        f"{layer} contains {len(thin_widths)} isolated shell(s) narrower "
                        f"than {profile.minimum_xy_feature_mm:.2f} mm",
                        severity="warning",
                    )
                )

        z_span = float(np.ptp(mesh.vertices[:, 2]))
        if layer != "base" and z_span < profile.minimum_z_feature_mm - 1e-6:
            issues.append(
                PrintabilityIssue(
                    "thin_z_feature",
                    layer,
                    f"{layer} is only {z_span:.2f} mm tall; the profile recommends "
                    f"at least {profile.minimum_z_feature_mm:.2f} mm",
                    severity="warning",
                )
            )

        if print_size_mm is not None and layer != "base":
            width, height = print_size_mm
            allowed_margin = max(0.0, float(margin_mm or 0.0))
            bounds = mesh.bounds
            tolerance = 0.03
            if (
                bounds[0, 0] < allowed_margin - tolerance
                or bounds[0, 1] < allowed_margin - tolerance
                or bounds[1, 0] > width - allowed_margin + tolerance
                or bounds[1, 1] > height - allowed_margin + tolerance
            ):
                issues.append(
                    PrintabilityIssue(
                        "outside_printable_margin",
                        layer,
                        f"{layer} extends outside the {allowed_margin:.1f} mm printable margin",
                        severity="warning",
                    )
                )

    route_components = [
        component for component in component_lookup.values() if component.layer == "route"
    ]
    meaningful_route_components = [
        component for component in route_components if len(component.face_indices) >= 4
    ]
    if len(meaningful_route_components) > 1:
        issues.append(
            PrintabilityIssue(
                "route_discontinuity",
                "route",
                f"route contains {len(meaningful_route_components)} disconnected printable sections",
            )
        )

    base_mesh = active.get("base")
    if base_mesh is not None and len(base_mesh.vertices):
        base_bottom = float(base_mesh.bounds[0, 2])
        for layer, mesh in active.items():
            if layer == "base" or not len(mesh.vertices):
                continue
            if float(mesh.bounds[0, 2]) < base_bottom - contact_tolerance_mm:
                issues.append(
                    PrintabilityIssue(
                        "outside_z_bounds",
                        layer,
                        f"{layer} extends below the base by "
                        f"{base_bottom - float(mesh.bounds[0, 2]):.2f} mm",
                    )
                )

    if invalid_coordinates or "base" not in active or any(
        issue.blocking for issue in issues
    ):
        if "base" not in active and not invalid_coordinates:
            issues.append(
                PrintabilityIssue(
                    "support_not_checked",
                    "model",
                    "No base mesh was supplied, so support paths could not be verified",
                    severity="warning",
                )
            )
        return PrintabilityReport(tuple(issues))

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
        issues.append(
            PrintabilityIssue("missing_support", "base", "base has no upward surface")
        )
        return PrintabilityReport(tuple(issues))

    tree = STRtree(surface_polygons)
    geometry_indices = {id(geometry): index for index, geometry in enumerate(surface_polygons)}
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
    "PrintabilityProfile",
    "PrintabilityReport",
    "audit_printability",
]
