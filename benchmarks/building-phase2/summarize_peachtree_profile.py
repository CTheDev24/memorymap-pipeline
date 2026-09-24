"""Summarize paired full cProfile replays and their exact artifact comparison."""

import argparse
import json
import platform
import pstats
from pathlib import Path

import geopandas
import numpy
import pandas
import shapely
import trimesh

FUNCTIONS = {
    'building_grouping.py': {'group_footprints', 'blocked'},
    'building_generalization.py': {'measure_footprints', 'local_core_width_mm', '_cache_width_batch'},
    'buildings.py': {'_tags_from_gdf_row'},
    'printability.py': {'_mesh_components', '_floating_components', 'audit_printability',
                        'remove_small_floating_components', 'floating', 'mesh_components'},
    'mesh.py': {'export_3mf', '_apply_3mf_materials'},
    'export.py': {'export_mesh'},
}


def read_run(folder):
    result = json.loads((folder / 'result.json').read_text())
    stats = result['stats']['building_generalization']
    events = [json.loads(line) for line in (folder / 'progress.jsonl').read_text().splitlines()]
    functions = {}
    for (filename, _line, name), value in pstats.Stats(str(folder / 'generation.prof')).stats.items():
        module = Path(filename).name
        if name in FUNCTIONS.get(module, set()):
            functions[f'{module}:{name}'] = {
                'calls': value[1], 'self_seconds': value[2], 'cumulative_seconds': value[3],
            }
    return {
        'generation_seconds_profiled': result['elapsed_seconds'],
        'building_stage_seconds': stats['stage_seconds'],
        'optimization_seconds': stats['optimization_seconds'],
        'export_audit_sidecar_seconds': next(e['seconds'] for e in events if e['percent'] == 100)
        - next(e['seconds'] for e in events if e['message'] == 'Building mesh complete'),
        'functions': functions,
        'mesh_checks': result['mesh_checks'],
        'route_overlap_mm2': stats['route_exclusion_projected_overlap_mm2'],
        'group_count': stats['group_count'],
        'grouped_source_count': stats['grouped_source_count'],
        'output_widths': stats['output_widths'],
        'warnings': result['warnings'],
        'progress_monotonic': result['progress_monotonic'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--comparison', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inputs = json.loads((args.before / 'inputs.json').read_text())
    assert inputs == json.loads((args.after / 'inputs.json').read_text())
    comparison = json.loads(args.comparison.read_text())
    assert comparison['matches']
    report = {
        'scope': 'Paired full frozen Peachtree generation with cProfile; excludes acquisition',
        'baseline': 'Inherited uncommitted performance work, not original Phase 2',
        'environment': {'platform': platform.platform(), 'python': platform.python_version(),
                        'numpy': numpy.__version__, 'shapely': shapely.__version__,
                        'geos': shapely.geos_version_string, 'pandas': pandas.__version__,
                        'geopandas': geopandas.__version__, 'trimesh': trimesh.__version__},
        'inputs': inputs,
        'before': read_run(args.before), 'after': read_run(args.after),
        'equivalence': comparison,
        'limitations': [
            'One paired run on a shared Windows workstation, not a throughput guarantee',
            'cProfile disproportionately affects Python-heavy loops; production gains may differ',
            'Short tests and one mask microbenchmark overlapped the baseline; after ran without them',
            'No slicing or physical-print validation',
        ],
    }
    before = report['before']['generation_seconds_profiled']
    after = report['after']['generation_seconds_profiled']
    report['profiled_seconds_saved'] = before - after
    report['profiled_reduction_percent'] = 100 * (before - after) / before
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Profiled seconds: {before:.2f} -> {after:.2f}; exact artifacts match')


if __name__ == '__main__':
    main()
