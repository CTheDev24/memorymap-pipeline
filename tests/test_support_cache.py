"""Support reuse must preserve audit failures and invalidate on mesh edits."""

from trimesh.creation import box
from trimesh.util import concatenate

from memorymap_pipeline import printability
from memorymap_pipeline.mesh import build_base_plate


def solid(z):
    mesh = box(extents=(2, 2, 1))
    mesh.apply_translation((5, 5, z))
    return mesh


def test_support_cache_reuses_analysis_but_detects_in_place_edits(monkeypatch):
    cache = printability._SupportCache()
    meshes = {'base': build_base_plate(10, 10, 1.6), 'buildings': solid(.4)}
    real = printability._floating_components
    calls = []

    def measured(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(printability, '_floating_components', measured)
    cleaned, count = printability.remove_small_floating_components(
        meshes, 'buildings', _cache=cache,
    )
    assert count == 0 and cleaned is meshes['buildings']
    assert printability.audit_printability(meshes, _cache=cache).printable
    assert len(calls) == 1
    meshes['buildings'].vertices[:, 2] += 4
    cached = printability.audit_printability(meshes, _cache=cache)
    assert len(calls) == 2
    assert not cached.printable
    assert cached == printability.audit_printability(meshes)


def test_support_cache_invalidates_settings_and_cleanup_results():
    cache = printability._SupportCache()
    meshes = {'base': build_base_plate(10, 10, 1.6), 'buildings': solid(.515)}
    assert printability.audit_printability(meshes, _cache=cache).printable
    assert not printability.audit_printability(
        meshes, contact_tolerance_mm=0, _cache=cache,
    ).printable
    meshes['buildings'] = concatenate([solid(.4), solid(5)])
    cleaned, count = printability.remove_small_floating_components(
        meshes, 'buildings', _cache=cache,
    )
    assert count == 1
    meshes['buildings'] = cleaned
    cached = printability.audit_printability(meshes, _cache=cache)
    assert cached.printable
    assert cached == printability.audit_printability(meshes)
    # Face count and vertex positions stay unchanged, but winding must still fail.
    meshes['buildings'].faces[0] = meshes['buildings'].faces[0][::-1]
    cached = printability.audit_printability(meshes, _cache=cache)
    assert not cached.printable
    assert cached == printability.audit_printability(meshes)
