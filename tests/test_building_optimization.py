"""Physical policy tests; unknown semantics stay unknown throughout."""
import json
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union

from memorymap_pipeline.building_classification import BuildingClass
from memorymap_pipeline.building_optimization import optimize_buildings, simplify_footprint
from memorymap_pipeline.buildings import BuildingDimensions, download_and_build_buildings
from memorymap_pipeline.map_frame import MapFrame
from memorymap_pipeline.print_scale import PrintScaleContext


def context():
    return PrintScaleContext.from_frame(MapFrame(0, 0, 100, 100, 100, 100),
                                       {'line_width_mm': .42, 'building_generalization_mode': 'print_optimized'})


def element(p, kind=BuildingClass.UNKNOWN, *, height=6, height_source='fallback', part=False, landmark=None):
    dims = BuildingDimensions(0, height, 0, 'flat', 'along', kind, height_source)
    return p, dims, part, landmark


def optimize(elements, heights=None, **kwargs):
    return optimize_buildings(elements, heights or [1.0]*len(elements), context(),
                              allowed_region=box(-20,-20,100,100), **kwargs)


def test_unknown_small_is_omitted_but_large_remains_unknown():
    result = optimize([element(box(0,0,.2,.2)), element(box(5,5,8,8))])
    assert result.omitted == {0}
    assert all(d['source_class'] == 'unknown' for d in result.decisions)
    assert result.decisions[1]['action'] == 'preserved'


def test_unknown_neighbors_group_without_semantic_reclassification():
    result = optimize([element(box(0,0,.5,.6)), element(box(.7,0,1.2,.6))], [1.0,2.0])
    assert len(result.groups) == 1
    assert not result.omitted
    assert result.group_heights[0] == 1.5
    assert all(d['source_class']=='unknown' and d['action']=='grouped' for d in result.decisions)


def test_explicit_tall_unknown_is_preserved_and_flagged():
    result = optimize([element(box(0,0,.3,.3),height=50,height_source='height')],[10])
    assert not result.omitted and not result.groups
    assert result.decisions[0]['action']=='unresolved'
    assert result.decisions[0]['policy_class']=='tall'


def test_barrier_prevents_neighborhood_merge():
    result = optimize([element(box(0,0,.5,.6)),element(box(.7,0,1.2,.6))],
                      barriers=box(.55,-1,.65,2))
    assert not result.groups and result.omitted == {0,1}


def test_simplification_removes_thin_appendage_without_expansion():
    p=unary_union([box(0,0,3,3),box(3,1.4,4,1.6)])
    q=simplify_footprint(p,context())
    assert q.is_valid and p.covers(q)
    assert q.area >= .8*p.area
    assert q.bounds[2] < 3.01


def test_courtyard_and_parent_part_contact_are_protected():
    courtyard=box(0,0,5,5).difference(box(.2,.2,4.8,4.8))
    result=optimize([element(courtyard),element(box(10,0,10.2,.2)),
                     element(box(10.2,0,10.4,.2),part=True)])
    assert not result.omitted and not result.groups
    assert len(result.footprints[0].interiors)==1
    assert all(d['action']=='unresolved' for d in result.decisions)


def test_route_cut_applies_to_protected_parts_and_checks_fragments():
    route=box(.3,-1,.7,2)
    result=optimize([element(box(0,0,1,1)),element(box(0,0,1,1),part=True)],
                    route_exclusion=route)
    assert all(p.intersection(route).area==0 for p in result.footprints)
    assert result.decisions[1]['route_clipped_area_mm2']>0
    ordinary=optimize([element(box(0,0,1,1))],route_exclusion=route)
    assert ordinary.omitted=={0}


def test_actual_peachtree_route_collision_is_removed():
    data=json.loads((Path(__file__).parent/'fixtures/peachtree_route_conflict.geojson').read_text())
    geoms={f['properties']['role']:shape(f['geometry']) for f in data['features']}
    assert geoms['building'].intersection(geoms['route']).area > .5
    result=optimize([element(geoms['building'])],[2.592],
                    route_exclusion=geoms['route'].buffer(context().route_clearance_mm))
    assert all(p.intersection(geoms['route']).area==0 for p in result.footprints)


def test_omitted_route_consumed_source_does_not_break_group_search():
    result=optimize([element(box(0,0,.2,.2)),element(box(5,0,5.5,.6)),
                    element(box(5.7,0,6.2,.6))],route_exclusion=box(-1,-1,1,1))
    assert 0 in result.omitted and len(result.groups)==1


def test_grouping_is_deterministic_under_input_order():
    elements=[element(box(x,0,x+.5,.6)) for x in (0,.7,1.4,5,5.7)]
    a=optimize(elements);b=optimize(elements[::-1])
    assert unary_union([g.geometry for g in a.groups]).equals(unary_union([g.geometry for g in b.groups]))


def test_protected_small_landmark_is_not_silently_dropped():
    result=optimize([element(box(0,0,.2,.2),BuildingClass.LANDMARK)])
    assert not result.omitted and result.decisions[0]['action']=='unresolved'


def test_roof_and_body_use_route_clipped_outline(tmp_path):
    frame=MapFrame(41.88,-87.63,200,200,20,20)
    p=box(7,7,12,12);xy=np.array(p.exterior.coords);lat,lon=frame.print_to_lonlat(xy[:,0],xy[:,1])
    source=tmp_path/'roof.geojson'
    source.write_text(json.dumps({'type':'FeatureCollection','features':[{
        'type':'Feature','properties':{'building':'house','height':12,'roof:height':3,'roof:shape':'gabled'},
        'geometry':{'type':'Polygon','coordinates':[list(zip(lon,lat))]}}]}))
    records=[];stats={};route=box(9,0,10,20)
    union,mesh=download_and_build_buildings(None,frame.center_lat,frame.center_lon,{'map_frame':frame},
        20,20,0,buildings_file=str(source),generalization_mode='print_optimized',
        print_scale_context=context(),route_exclusion=route,diagnostics=records,generalization_stats=stats)
    assert mesh is not None and mesh.is_watertight
    # No triangle, including a roof triangle, may bridge the protected corridor.
    tri=mesh.triangles[:,:,:2]
    for points in tri:
        assert Polygon(points).intersection(route).area < 1e-8
    assert union.intersection(route).area==0
    assert stats['thresholds_applied_to_geometry']
    assert records[0]['optimization']['source_class']=='residential'


def test_copy_model_keeps_matching_audit_and_removes_stale_report(tmp_path):
    from memorymap_pipeline.desktop.project import copy_generation_result
    a=tmp_path/'a.3mf';b=tmp_path/'b.3mf';a.write_bytes(b'model')
    a.with_suffix('.audit.json').write_text('{"mode":"print_optimized"}')
    copy_generation_result(a,b)
    assert b.with_suffix('.audit.json').read_text()==a.with_suffix('.audit.json').read_text()
    a.with_suffix('.audit.json').unlink();copy_generation_result(a,b)
    assert not b.with_suffix('.audit.json').exists()


def test_optimized_generation_exports_source_decisions_and_disables_legacy_filter(tmp_path):
    from memorymap_pipeline.generation import GenerationRequest, generate_memory_map
    from memorymap_pipeline.gpx_loader import load_route_from_gpx
    fixtures=Path(__file__).parent/'fixtures'
    route=load_route_from_gpx(fixtures/'frame_route.gpx')
    frame=MapFrame.fit_route(route.points,120,90,5)
    result=generate_memory_map(GenerationRequest(route=route,frame=frame,output_path=tmp_path/'map.3mf',
        roads_file=fixtures/'frame_roads.geojson',buildings_file=fixtures/'frame_buildings.geojson',
        water_file=fixtures/'frame_water.geojson',config={'building_generalization_mode':'print_optimized',
        'line_width_mm':.42,'residential_min_width_mm':99}))
    report=json.loads(result.output_path.with_suffix('.audit.json').read_text())
    assert report['settings']['residential_min_width_mm']==0
    assert report['statistics']['building_generalization']['thresholds_applied_to_geometry']
    assert report['buildings'] and len(report['model_sha256'])==64
    generate_memory_map(GenerationRequest(route=route,frame=frame,output_path=result.output_path,
        include_roads=False,include_buildings=False))
    assert not result.output_path.with_suffix('.audit.json').exists()



def test_insignificant_hole_is_removed_only_without_protected_content():
    p=box(0,0,3,3).difference(box(1,1,1.2,1.2))
    assert len(simplify_footprint(p,context()).interiors)==0
    assert len(simplify_footprint(p,context(),forbidden=box(1,1,1.2,1.2)).interiors)==1


def test_optimized_project_roundtrip_and_legacy_default():
    from memorymap_pipeline.desktop.project import DesktopProject
    project=DesktopProject(frame=MapFrame(0,0,100,100,100,100),
                           building_generalization_mode='print_optimized',building_line_width_mm=.45)
    assert DesktopProject.from_dict(project.to_dict()).to_dict()==project.to_dict()
    data=project.to_dict();data.pop('building_generalization_mode');data.pop('building_line_width_mm')
    assert DesktopProject.from_dict(data).building_generalization_mode=='manual'
