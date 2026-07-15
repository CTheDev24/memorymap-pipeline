from __future__ import annotations

import time
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from xml.etree import ElementTree as ET

from memorymap_pipeline.api import create_app


GPX = b'''<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="29.7600" lon="-95.3700"/><trkpt lat="29.7610" lon="-95.3690"/>
</trkseg></trk></gpx>'''


def upload(client: TestClient) -> dict:
    response = client.post("/api/routes", files={"file": ("sample.gpx", GPX, "application/gpx+xml")})
    assert response.status_code == 201
    return response.json()


def test_upload_returns_map_contract(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        body = upload(client)
        assert body["point_count"] == 2
        assert body["route"]["geometry"]["type"] == "LineString"
        assert body["bounds"]["north"] == 29.761
        assert body["suggested_frame"]["coverage_width_m"] > 0


def test_upload_rejects_invalid_gpx(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/routes", files={"file": ("bad.gpx", b"nope")})
        assert response.status_code == 422


def test_preview_and_generate(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        route = upload(client)
        frame = route["suggested_frame"]
        preview = client.post(f"/api/routes/{route['id']}/preview", json={"frame": frame})
        assert preview.status_code == 200
        assert preview.json()["route"]["geometry"]["type"] in ("LineString", "MultiLineString")
        generated = client.post(f"/api/routes/{route['id']}/generate", json={"frame": frame})
        assert generated.status_code == 202
        job_id = generated.json()["id"]
        for _ in range(100):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("complete", "failed"):
                break
            time.sleep(.02)
        assert job["status"] == "complete", job
        result = client.get(job["result_url"])
        assert result.status_code == 200
        assert zipfile.is_zipfile(BytesIO(result.content))

        with zipfile.ZipFile(BytesIO(result.content)) as archive:
            model_name = next(name for name in archive.namelist() if name.lower().endswith(".model"))
            root = ET.fromstring(archive.read(model_name))
        namespace = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
        z_values = [float(item.attrib["z"]) for item in root.findall(".//m:vertex", namespace)]
        assert min(z_values) == pytest.approx(-1.6)
        assert max(z_values) == pytest.approx(2.0)


def test_frame_validation(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        route = upload(client)
        frame = route["suggested_frame"]
        frame["margin_mm"] = 1000
        response = client.post(f"/api/routes/{route['id']}/preview", json={"frame": frame})
        assert response.status_code == 422
