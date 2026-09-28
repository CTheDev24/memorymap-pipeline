"""Bounded, resumable building downloads for large print frames."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path

import requests


def tiles(bbox, edge_m=4000):
    south, north, west, east = bbox
    rows = max(1, math.ceil((north - south) * 111000 / edge_m))
    cols = max(1, math.ceil((east - west) * 111000 * math.cos(math.radians((south + north) / 2)) / edge_m))
    return [(south + (north-south)*r/rows, south + (north-south)*(r+1)/rows,
             west + (east-west)*c/cols, west + (east-west)*(c+1)/cols)
            for r in range(rows) for c in range(cols)]


def download_tiles(bbox, endpoints, progress=None, cache_dir=None, diagnostics=None):
    cache = Path(cache_dir) if cache_dir else Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Trace Studio' / 'building-cache-v1'
    cache.mkdir(parents=True, exist_ok=True)
    result = {}
    stats = diagnostics if diagnostics is not None else {}
    stats.update(mode="tiled", status="downloading", cached_tiles=0, downloaded_tiles=0)
    blocked = set()
    parts = tiles(bbox)
    stats["tile_count"] = len(parts)
    for index, (south, north, west, east) in enumerate(parts, 1):
        if progress:
            progress(0.07 * (index-1)/len(parts), f'Downloading building area {index}/{len(parts)}')
        query = (f'[out:json][timeout:60];('
                 + ''.join(f'{kind}["{tag}"]({south},{west},{north},{east});'
                           for kind in ('way', 'relation') for tag in ('building', 'building:part'))
                 + ');out body geom;')
        target = cache / (hashlib.sha256(query.encode()).hexdigest() + '.json')
        data = None
        try:
            candidate = json.loads(target.read_text())
            if isinstance(candidate.get('elements'), list) and not candidate.get('remark'):
                data = candidate
                stats["cached_tiles"] += 1
        except (OSError, ValueError, AttributeError):
            pass
        errors = []
        if data is None:
            for endpoint in endpoints:
                if endpoint in blocked:
                    continue
                try:
                    response = requests.post(endpoint, data={'data': query},
                                             headers={'User-Agent': 'TraceStudio/1.0', 'Accept': 'application/json'},
                                             timeout=(10, 75))
                    if response.status_code in (401, 403, 429):
                        blocked.add(endpoint)
                    response.raise_for_status()
                    candidate = response.json()
                    if candidate.get('remark') or not isinstance(candidate.get('elements'), list):
                        raise ValueError('Incomplete Overpass response')
                    data = candidate
                    break
                except (requests.RequestException, ValueError, AttributeError) as exc:
                    errors.append(f'{endpoint}: {exc}')
                    logging.warning("Building area %s/%s failed via %s: %s", index, len(parts), endpoint, exc)
            if data is None:
                stats["status"] = "failed"
                stats["failed_tile"] = index
                raise RuntimeError(f'Building download failed for area {index}/{len(parts)}. '
                                   'Model generation stopped to avoid an incomplete building layer. '
                                   'Retry to reuse cached successful areas. ' + '; '.join(errors))
            stats["downloaded_tiles"] += 1
            temp = target.with_suffix(f'.{os.getpid()}.tmp')
            temp.write_text(json.dumps(data))
            temp.replace(target)
        for element in data['elements']:
            if element.get('type') in ('way', 'relation') and 'id' in element:
                result[(element['type'], element['id'])] = element
    stats.update(status="complete", source_elements=len(result))
    return list(result.values())
