import numpy as np
import pytest
from shapely.geometry import box
from trimesh import Trimesh
from trimesh.creation import box as create_box

from memorymap_pipeline import mesh as mesh_module


def test_route_mesh_from_polygon_rejects_non_watertight_cleanup(monkeypatch) -> None:
    raw = Trimesh(
        vertices=np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [1.0, 1.0, 0.0],
                [0.0, 1.0, 0.0],
            ]
        ),
        faces=np.array(
            [
                [0, 1, 2],
                [0, 2, 3],
                [0, 0, 1],
            ]
        ),
        process=False,
    )

    monkeypatch.setattr(
        mesh_module, "extrude_polygon", lambda *_args, **_kwargs: raw.copy()
    )

    with pytest.raises(ValueError, match="non-watertight"):
        mesh_module.route_mesh_from_polygon(
            box(0.0, 0.0, 1.0, 1.0), 1.0, z_offset=0.5
        )


def test_route_mesh_from_polygon_keeps_watertight_mesh_after_cleanup(monkeypatch) -> None:
    raw = create_box(extents=(1.0, 1.0, 1.0))
    raw = Trimesh(
        vertices=np.asarray(raw.vertices, dtype=float),
        faces=np.vstack(
            [
                np.asarray(raw.faces, dtype=int),
                np.array([[0, 0, 1]], dtype=int),
            ]
        ),
        process=False,
    )

    monkeypatch.setattr(
        mesh_module, "extrude_polygon", lambda *_args, **_kwargs: raw.copy()
    )

    result = mesh_module.route_mesh_from_polygon(box(0.0, 0.0, 1.0, 1.0), 1.0, z_offset=0.5)

    assert result.is_watertight
    assert np.all(np.isfinite(result.area_faces))
    assert np.all(result.area_faces > 1e-12)
