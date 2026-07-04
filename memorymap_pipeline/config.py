from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "portrait": {"map_width": 190.0, "map_height": 240.0},
    "landscape": {"map_width": 240.0, "map_height": 1900.0},
    "route_height": 2.0,
    "route_width": 1.2,
    "base_thickness": 1.0,
    "margin": 8.0,
}


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    if config_path is None:
        return config

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    loaded = json.loads(path.read_text(encoding="utf-8"))
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value

    return config
