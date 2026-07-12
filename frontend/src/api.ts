import type{Feature,LineString}from'geojson';export type Orientation='landscape'|'portrait';
export type Frame={center:{latitude:number;longitude:number};center_lat:number;center_lon:number;coverage_width_m:number;coverage_height_m:number;rotation_degrees:number;orientation:Orientation;print_width_mm:number;print_height_mm:number;margin_mm:number};
export type RouteUpload={route_id:string;geojson:Feature<LineString>;bounds:[number,number,number,number];suggested_frame:Frame};
export type Settings={frame:Frame;layers:{route:boolean;roads:boolean;buildings:boolean};route_width_mm:number};
const json=async<T>(r:Response):Promise<T>=>{if(!r.ok)throw new Error((await r.text())||`Request failed (${r.status})`);return r.json()};
const payload=(s:Settings)=>({...s,frame:{...s.frame,center_lat:s.frame.center.latitude,center_lon:s.frame.center.longitude}});
export async function uploadRoute(file:File){const form=new FormData();form.append('file',file);const raw=await json<any>(await fetch('/api/routes',{method:'POST',body:form}));const f=raw.suggested_frame;return{route_id:raw.id,geojson:raw.route,bounds:[raw.bounds.west,raw.bounds.south,raw.bounds.east,raw.bounds.north]as[number,number,number,number],suggested_frame:{...f,center:{latitude:f.center_lat,longitude:f.center_lon},orientation:f.print_width_mm>=f.print_height_mm?'landscape':'portrait'}as Frame}}
export async function preview(id:string,s:Settings){return json<{warnings?:string[]}>(await fetch(`/api/routes/${id}/preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload(s))}))}
export async function generate(id:string,s:Settings){const raw=await json<any>(await fetch(`/api/routes/${id}/generate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload(s))}));return{job_id:raw.id}}
export async function getJob(id:string){return json<{status:string;progress?:number;warnings?:string[];result_url?:string}>(await fetch(`/api/jobs/${id}`))}
