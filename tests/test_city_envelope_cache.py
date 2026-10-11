from shapely import from_wkb
from shapely.geometry import MultiPolygon, box
from shapely.geometry.base import BaseGeometry

from memorymap_pipeline.city_relief import _OccupiedEnvelopeCache


def occupied():
    return MultiPolygon([box(i * 2, 0, i * 2 + 1, 1) for i in range(8)])


def test_exact_geometry_and_radius_reuse_without_reusing_changed_input(monkeypatch):
    polygon = occupied()
    candidate = box(0, 0, 1.2, 1)
    original = BaseGeometry.buffer
    calls = []

    def counted(geometry, radius, *args, **kwargs):
        calls.append((geometry.wkb, radius))
        return original(geometry, radius, *args, **kwargs)

    expected = original(polygon, 0.42).covers(candidate)
    monkeypatch.setattr(BaseGeometry, "buffer", counted)
    cache = _OccupiedEnvelopeCache()
    assert cache.covers(polygon, candidate, 0.42) == expected
    assert cache.covers(from_wkb(polygon.wkb), candidate, 0.42) == expected
    assert len(calls) == 1
    cache.covers(polygon, candidate, 0.43)
    changed = MultiPolygon([box(0.001, 0, 1, 1), *list(polygon.geoms)[1:]])
    assert cache.covers(changed, candidate, 0.42) == original(changed, 0.42).covers(candidate)
    assert len(calls) == 3


def test_oversized_geometry_uses_original_predicate_without_retention():
    polygon = occupied()
    candidate = box(0, 0, 16, 1)
    cache = _OccupiedEnvelopeCache(max_bytes=1)
    assert cache.covers(polygon, candidate, 0.42) == polygon.buffer(0.42).covers(candidate)
    assert cache.bytes == 0 and not cache.entries
