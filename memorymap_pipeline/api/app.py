from __future__ import annotations

import math
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from shapely.geometry import LineString, box, mapping, shape

from ..gpx_loader import Route, load_route_from_gpx
from ..mesh import build_base_plate, export_3mf, route_mesh_from_polygon
from ..projection import project_points
from .models import GenerateRequest, JobStatus, MapFrame, PreviewRequest

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@dataclass
class RouteRecord:
    directory: Path
    route: Route


@dataclass
class JobRecord:
    id: str
    directory: Path
    status: str = "queued"
    progress: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    result: Path | None = None


class ApiState:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.routes: dict[str, RouteRecord] = {}
        self.jobs: dict[str, JobRecord] = {}
        self.lock = threading.Lock()


def _bounds(route: Route) -> dict[str, float]:
    lat = [p.latitude for p in route.points]
    lon = [p.longitude for p in route.points]
    return {"south": min(lat), "west": min(lon), "north": max(lat), "east": max(lon)}


def _geojson(route: Route) -> dict:
    return {"type": "Feature", "properties": {}, "geometry": {
        "type": "LineString", "coordinates": [[p.longitude, p.latitude] for p in route.points]}}


def _suggest_frame(route: Route) -> MapFrame:
    bounds = _bounds(route)
    lat = (bounds["south"] + bounds["north"]) / 2
    lon = (bounds["west"] + bounds["east"]) / 2
    projected = project_points(route.points, lat, lon)
    width = max(float(np.ptp(projected[:, 0])), 10.0) * 1.12
    height = max(float(np.ptp(projected[:, 1])), 10.0) * 1.12
    print_width, print_height = (240.0, 190.0) if width >= height else (190.0, 240.0)
    aspect = print_width / print_height
    if width / height < aspect:
        width = height * aspect
    else:
        height = width / aspect
    return MapFrame(center_lat=lat, center_lon=lon, coverage_width_m=width,
                    coverage_height_m=height, print_width_mm=print_width,
                    print_height_mm=print_height)


def _frame_points(route: Route, frame: MapFrame) -> np.ndarray:
    points = project_points(route.points, frame.center_lat, frame.center_lon)
    angle = math.radians(-frame.rotation_degrees)
    rotation = np.array([[math.cos(angle), -math.sin(angle)],
                         [math.sin(angle), math.cos(angle)]])
    points = points @ rotation.T
    width = frame.print_width_mm - 2 * frame.margin_mm
    height = frame.print_height_mm - 2 * frame.margin_mm
    points[:, 0] = (points[:, 0] / frame.coverage_width_m + .5) * width + frame.margin_mm
    points[:, 1] = (points[:, 1] / frame.coverage_height_m + .5) * height + frame.margin_mm
    return points


def _preview(route: Route, frame: MapFrame) -> tuple[dict, list[str]]:
    line = LineString(_frame_points(route, frame))
    printable = box(frame.margin_mm, frame.margin_mm,
                    frame.print_width_mm - frame.margin_mm,
                    frame.print_height_mm - frame.margin_mm)
    clipped = line.intersection(printable)
    warnings = []
    if clipped.is_empty:
        warnings.append("The route does not intersect the selected frame.")
    elif not printable.covers(line):
        warnings.append("Part of the route is outside the selected frame and will be clipped.")
    feature = {"type": "Feature", "properties": {"units": "mm"}, "geometry": mapping(clipped)}
    return feature, warnings


def _run_generation(job: JobRecord, route: Route, request: GenerateRequest) -> None:
    try:
        job.status, job.progress = "running", 10
        feature, warnings = _preview(route, request.frame)
        job.warnings.extend(warnings)
        clipped = shape(feature["geometry"])
        if clipped.is_empty:
            raise ValueError("Route does not intersect the selected frame")
        polygon = clipped.buffer(request.route_width_mm / 2, cap_style=1, join_style=1)
        job.progress = 55
        route_mesh = route_mesh_from_polygon(polygon, request.route_height_mm,
                                             z_offset=request.base_thickness_mm)
        base = build_base_plate(request.frame.print_width_mm, request.frame.print_height_mm,
                                request.base_thickness_mm)
        if request.layers.roads:
            job.warnings.append("Road generation is not yet available in API jobs.")
        if request.layers.buildings:
            job.warnings.append("Building generation is not yet available in API jobs.")
        output = job.directory / "memorymap.3mf"
        export_3mf(output, base, route_mesh)
        job.result, job.progress, job.status = output, 100, "complete"
    except Exception as exc:
        job.error, job.status = str(exc), "failed"


def create_app(workspace: str | Path | None = None) -> FastAPI:
    owned = workspace is None
    root = Path(workspace) if workspace else Path(tempfile.mkdtemp(prefix="memorymap-api-"))
    root.mkdir(parents=True, exist_ok=True)
    state = ApiState(root)
    app = FastAPI(title="MemoryMap API", version="0.1.0")

    @app.on_event("shutdown")
    def cleanup() -> None:
        if owned:
            shutil.rmtree(root, ignore_errors=True)

    def route_for(route_id: str) -> RouteRecord:
        if route_id not in state.routes:
            raise HTTPException(404, "Route not found")
        return state.routes[route_id]

    @app.post("/api/routes", status_code=201)
    async def upload_route(file: UploadFile = File(...)) -> dict:
        if not file.filename or Path(file.filename).suffix.lower() != ".gpx":
            raise HTTPException(415, "A .gpx file is required")
        route_id = uuid.uuid4().hex
        directory = root / "routes" / route_id
        directory.mkdir(parents=True)
        path = directory / "route.gpx"
        size = 0
        try:
            with path.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "GPX exceeds 25 MB")
                    destination.write(chunk)
            route = load_route_from_gpx(path)
        except HTTPException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(422, f"Invalid GPX: {exc}") from exc
        finally:
            await file.close()
        with state.lock:
            state.routes[route_id] = RouteRecord(directory, route)
        return {"id": route_id, "filename": Path(file.filename).name,
                "point_count": len(route.points), "route": _geojson(route),
                "bounds": _bounds(route), "suggested_frame": _suggest_frame(route).model_dump()}

    @app.post("/api/routes/{route_id}/preview")
    def preview(route_id: str, request: PreviewRequest) -> dict:
        feature, warnings = _preview(route_for(route_id).route, request.frame)
        return {"route": feature if request.layers.route else None,
                "roads": None, "buildings": None, "warnings": warnings}

    def status_for(job: JobRecord) -> JobStatus:
        return JobStatus(id=job.id, status=job.status, progress=job.progress,
                         warnings=job.warnings, error=job.error,
                         result_url=f"/api/jobs/{job.id}/result" if job.status == "complete" else None)

    @app.post("/api/routes/{route_id}/generate", status_code=202)
    def generate(route_id: str, request: GenerateRequest) -> JobStatus:
        route = route_for(route_id).route
        job_id = uuid.uuid4().hex
        directory = root / "jobs" / job_id
        directory.mkdir(parents=True)
        job = JobRecord(job_id, directory)
        state.jobs[job_id] = job
        threading.Thread(target=_run_generation, args=(job, route, request), daemon=True).start()
        return status_for(job)

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> JobStatus:
        if job_id not in state.jobs:
            raise HTTPException(404, "Job not found")
        return status_for(state.jobs[job_id])

    @app.get("/api/jobs/{job_id}/result")
    def job_result(job_id: str) -> FileResponse:
        if job_id not in state.jobs:
            raise HTTPException(404, "Job not found")
        job = state.jobs[job_id]
        if job.status != "complete" or job.result is None:
            raise HTTPException(409, "Job result is not ready")
        return FileResponse(job.result, media_type="model/3mf", filename="memorymap.3mf")

    return app


app = create_app()
