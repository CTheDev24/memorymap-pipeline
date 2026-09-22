from types import SimpleNamespace

import geopandas as gpd
import pytest
import requests

from memorymap_pipeline import buildings, roads
from memorymap_pipeline.overpass import MapDataDownloadError, graph_from_bounds, overpass_settings


def test_settings_restore_after_failure():
    settings = SimpleNamespace(overpass_url="original", overpass_endpoint="legacy", requests_timeout=20)
    ox = SimpleNamespace(settings=settings)
    with pytest.raises(RuntimeError):
        with overpass_settings(ox, "https://example.test/api/interpreter"):
            assert settings.overpass_url == settings.overpass_endpoint == "https://example.test/api"
            assert settings.requests_timeout == 120
            raise RuntimeError("timeout")
    assert vars(settings) == dict(overpass_url="original", overpass_endpoint="legacy", requests_timeout=20)


def test_bbox_order_for_both_osmnx_versions():
    def modern(bbox, *, network_type):
        assert bbox == (-118.5, 34.0, -118.2, 34.2)
        return network_type
    def legacy(north, south, east, west, *, network_type):
        assert (north, south, east, west) == (34.2, 34.0, -118.2, -118.5)
        return network_type
    for function in (modern, legacy):
        assert graph_from_bounds(SimpleNamespace(graph_from_bbox=function),
                                 (34.0, 34.2, -118.5, -118.2), "all") == "all"


def test_road_retry_changes_actual_host_and_stops_on_exhaustion(monkeypatch):
    import osmnx as ox
    original = (ox.settings.overpass_url, ox.settings.requests_timeout)
    calls = []
    def unavailable(bbox, *, network_type):
        calls.append((ox.settings.overpass_url, ox.settings.requests_timeout, bbox))
        raise requests.Timeout("offline")
    monkeypatch.setattr(ox, "graph_from_bbox", unavailable)
    with pytest.raises(MapDataDownloadError, match="Road download failed"):
        roads.download_and_build_roads((34, 34.2, -118.5, -118.2), 34.1, -118.3,
                                       {}, ["residential"], {}, .4)
    assert [c[0] for c in calls] == [e.removesuffix("/interpreter") for e in roads.OVERPASS_ENDPOINTS]
    assert all(c[1] == 120 and c[2] == (-118.5, 34, -118.2, 34.2) for c in calls)
    assert (ox.settings.overpass_url, ox.settings.requests_timeout) == original


@pytest.mark.parametrize("response", [None, {"elements": [], "remark": "runtime error: timed out"}, {"elements": []}])
def test_building_failure_is_distinct_from_empty_data(monkeypatch, response):
    import osmnx as ox
    calls = []
    def unavailable(*args, **kwargs):
        raise requests.Timeout("offline")
    def post(endpoint, **kwargs):
        calls.append(endpoint)
        assert kwargs["timeout"] == 120
        assert "[timeout:110]" in kwargs["data"]["data"]
        if response is None:
            raise requests.Timeout("offline")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: response)
    monkeypatch.setattr(ox, "features_from_bbox", unavailable)
    monkeypatch.setattr(requests, "post", post)
    args = ((34, 34.2, -118.5, -118.2), 34.1, -118.3, {}, 190, 240, 5)
    if response == {"elements": []}:
        assert buildings.download_and_build_buildings(*args) == (None, None)
        assert len(calls) == 1
    else:
        with pytest.raises(MapDataDownloadError, match="Building download failed"):
            buildings.download_and_build_buildings(*args)
        assert calls == buildings.OVERPASS_ENDPOINTS


def test_successful_empty_osmnx_buildings_do_not_retry(monkeypatch):
    import osmnx as ox
    monkeypatch.setattr(ox, "features_from_bbox", lambda *a, **kw: gpd.GeoDataFrame(geometry=[]))
    assert buildings.download_and_build_buildings(
        (34, 34.2, -118.5, -118.2), 34.1, -118.3, {}, 190, 240, 5
    ) == (None, None)
