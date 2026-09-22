"""Shared Overpass configuration for road and building downloads."""
from contextlib import contextmanager
from inspect import signature

OVERPASS_TIMEOUT = 120


class MapDataDownloadError(RuntimeError):
    """Requested map data could not be fetched; do not export a partial map."""


def configure_overpass(osmnx, endpoint):
    # OSMnx appends /interpreter itself, in both supported API generations.
    base_url = endpoint.rstrip("/").removesuffix("/interpreter")
    settings = osmnx.settings
    for name in ("overpass_url", "overpass_endpoint"):
        if hasattr(settings, name):
            setattr(settings, name, base_url)
    settings.requests_timeout = OVERPASS_TIMEOUT


@contextmanager
def overpass_settings(osmnx, endpoint):
    settings = osmnx.settings
    previous = {name: getattr(settings, name) for name in
                ("overpass_url", "overpass_endpoint", "requests_timeout")
                if hasattr(settings, name)}
    try:
        configure_overpass(osmnx, endpoint)
        yield
    finally:
        for name, value in previous.items():
            setattr(settings, name, value)


def graph_from_bounds(osmnx, bbox, network_type):
    south, north, west, east = bbox
    if "north" in signature(osmnx.graph_from_bbox).parameters:
        return osmnx.graph_from_bbox(north, south, east, west, network_type=network_type)
    return osmnx.graph_from_bbox((west, south, east, north), network_type=network_type)
