import pytest
from memorymap_pipeline import building_download as d


def test_tiles_cover_frame():
    parts = d.tiles((34, 34.18, -118.5, -118.25))
    assert len(parts) > 4
    assert min(p[0] for p in parts) == 34
    assert max(p[1] for p in parts) == 34.18
    assert sum((p[1]-p[0])*(p[3]-p[2]) for p in parts) == pytest.approx(.18*.25)


def test_cache_dedup_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'tiles', lambda bbox: [(0,1,0,1), (1,2,0,1)])
    calls = []
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {'elements': [{'type':'way', 'id':1}]}
    def post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2: raise d.requests.Timeout('failed')
        return Response()
    monkeypatch.setattr(d.requests, 'post', post)
    with pytest.raises(RuntimeError, match='area 2/2'):
        d.download_tiles(None, ['example'], cache_dir=tmp_path)
    assert len(list(tmp_path.glob('*.json'))) == 1
    assert len(d.download_tiles(None, ['example'], cache_dir=tmp_path)) == 1
    assert len(calls) == 3


def test_partial_response_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'tiles', lambda bbox: [(0,1,0,1)])
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {'elements': [], 'remark': 'runtime error: timeout'}
    monkeypatch.setattr(d.requests, 'post', lambda *a, **kw: Response())
    with pytest.raises(RuntimeError): d.download_tiles(None, ['example'], cache_dir=tmp_path)
    assert not list(tmp_path.glob('*.json'))


def test_large_frame_uses_tiles_even_with_radius(monkeypatch):
    from memorymap_pipeline.buildings import download_and_build_buildings
    def fail(*args, **kwargs):
        raise RuntimeError("tiled failure propagated")
    monkeypatch.setattr(d, "download_tiles", fail)
    with pytest.raises(RuntimeError, match="tiled failure propagated"):
        download_and_build_buildings(
            bbox=(33.99, 34.16, -118.49, -118.22), radius_m=15000,
            center_lat=34.075, center_lon=-118.355, transform={},
            map_width_mm=240, map_height_mm=190, margin_mm=0,
        )


def test_empty_success_cached_and_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "tiles", lambda bbox: [(0,1,0,1)])
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"elements": []}
    monkeypatch.setattr(d.requests, "post", lambda *a, **kw: Response())
    stats = {}
    assert d.download_tiles(None, ["example"], cache_dir=tmp_path, diagnostics=stats) == []
    assert stats["downloaded_tiles"] == 1 and stats["status"] == "complete"
    def unexpected(*args, **kwargs): raise AssertionError("Network used despite cache")
    monkeypatch.setattr(d.requests, "post", unexpected)
    assert d.download_tiles(None, ["example"], cache_dir=tmp_path, diagnostics=stats) == []
    assert stats["cached_tiles"] == 1
