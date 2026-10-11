"""Source fingerprint bundled identically with desktop and generation service."""

import hashlib
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def build_identity():
    import marshal
    from importlib import resources
    from importlib.util import find_spec
    from types import CodeType

    def normalized(c):
        return c.replace(
            co_filename="<source>",
            co_consts=tuple(normalized(x) if isinstance(x, CodeType) else x for x in c.co_consts),
        )

    digest = hashlib.sha256()
    for name in (
        "buildings",
        "building_download",
        "overpass_geometry",
        "building_optimization",
        "building_grouping",
        "spatial_area",
        "building_generalization",
        "building_heights",
        "city_relief",
        "map_styles",
        "street_selection",
        "generation",
        "roads",
        "mesh",
        "printability",
        "config",
        "desktop.project",
        "desktop.window",
        "desktop.worker",
    ):
        fullname = "memorymap_pipeline." + name
        code = find_spec(fullname).loader.get_code(fullname)
        digest.update(marshal.dumps(normalized(code)))
    for asset in ("data/landmarks.v1.json", "desktop/viewer/index.html"):
        digest.update(resources.files("memorymap_pipeline").joinpath(asset).read_bytes())
    return {
        "release": "city-relief-v1",
        "source_sha256": digest.hexdigest(),
        "packaged": bool(getattr(sys, "frozen", False)),
    }
