import numpy as np
from shapely.geometry import box
from trimesh import Trimesh

from memorymap_pipeline import mesh as mesh_module


def test_route_mesh_from_polygon_drops_zero_area_faces(monkeypatch) -> None:
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

    result = mesh_module.route_mesh_from_polygon(box(0.0, 0.0, 1.0, 1.0), 1.0, z_offset=0.5)

    assert len(result.faces) == 2
    assert np.all(result.area_faces > 1e-12)
    assert result.vertices[:, 2].min() == 0.5

