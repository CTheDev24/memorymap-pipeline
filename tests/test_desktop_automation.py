import json
from hashlib import sha256
from pathlib import Path

import pytest

from memorymap_pipeline.desktop.automation import load_job, verify_result, write_status
from memorymap_pipeline.desktop.project import DesktopProject
from memorymap_pipeline.map_frame import MapFrame


def job(tmp_path):
    route = Path("tests/fixtures/frame_route.gpx").resolve()
    project = DesktopProject(MapFrame(29.76, -95.37, 1000, 1400, 190, 240),
                             gpx_path=str(route), include_roads=False,
                             include_buildings=False)
    project.save(tmp_path / "project.json")
    request = tmp_path / "job.json"
    request.write_text(json.dumps({"version": 1, "project": "project.json",
                                   "job_directory": "result"}))
    return request


def test_job_resolves_relative_paths_and_refuses_reuse(tmp_path):
    request = job(tmp_path)
    project, route, directory, sources = load_job(request)
    assert route.is_absolute() and route.is_file()
    assert not project.include_buildings and not project.include_roads
    assert directory == tmp_path / "result"
    assert sources == {}
    with pytest.raises(FileExistsError):
        load_job(request)


def test_job_rejects_unknown_input_before_creating_output(tmp_path):
    request = job(tmp_path)
    value = json.loads(request.read_text())
    value["sources"] = {"elevation_grid": "secret.json"}
    request.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Unsupported frozen"):
        load_job(request)
    assert not (tmp_path / "result").exists()


def test_result_requires_matching_audit_and_no_failed_layers(tmp_path):
    model = tmp_path / "map.3mf"
    model.write_bytes(b"new model")
    audit = model.with_suffix(".audit.json")
    audit.write_text(json.dumps({"model_sha256": "old model"}))
    with pytest.raises(ValueError, match="match"):
        verify_result(model)
    digest = sha256(model.read_bytes()).hexdigest()
    audit.write_text(json.dumps({"model_sha256": digest, "layer_states": {"roads": "failed"}}))
    with pytest.raises(ValueError, match="failed layer"):
        verify_result(model)
    audit.write_text(json.dumps({"model_sha256": digest, "layer_states": {"roads": "disabled"}}))
    assert verify_result(model)["model_sha256"] == digest


def test_status_replaces_previous_snapshot(tmp_path):
    write_status(tmp_path, {"state": "running"})
    write_status(tmp_path, {"state": "failed", "error": "offline"})
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "failed"
    assert not (tmp_path / "status.tmp").exists()


def test_both_desktop_entry_points_dispatch_explicit_job(monkeypatch, tmp_path):
    import sys

    from memorymap_pipeline.desktop import automation, run
    from memorymap_pipeline.desktop.launcher import main

    calls = []
    monkeypatch.setattr(automation, "run", lambda path: calls.append(path) or 0)
    target = str(tmp_path / "job.json")
    monkeypatch.setattr(sys, "argv", ["Trace Studio.exe", "--automation", target])
    assert run() == 0
    assert main(["memorymap-desktop", "--automation", target]) == 0
    assert calls == [target, target]
    monkeypatch.setattr(sys, "argv", ["Trace Studio.exe", "--automation"])
    assert run() == 2
    assert main(["memorymap-desktop", "--automation"]) == 2
