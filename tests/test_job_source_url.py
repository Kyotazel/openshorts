"""Source URL persistence for the self-host History list."""
import asyncio
import json
import os

import httpx
import pytest

app_module = pytest.importorskip("app")


def test_job_source_url_skips_python_unbuffered_flag():
    job = {"cmd": ["/usr/bin/python", "-u", "main.py", "-u",
                   "https://www.youtube.com/watch?v=abc", "--keep-original"]}
    assert app_module._job_source_url(job) == "https://www.youtube.com/watch?v=abc"


def test_job_source_url_prefers_long_option():
    job = {"cmd": ["/usr/bin/python", "-u", "main.py", "--url",
                   "https://youtu.be/xyz"]}
    assert app_module._job_source_url(job) == "https://youtu.be/xyz"


def test_job_source_url_none_for_upload_cmd():
    job = {"cmd": ["/usr/bin/python", "-u", "main.py", "-i", "/tmp/x.mp4"]}
    assert app_module._job_source_url(job) is None


def test_persist_and_listed_source_url_from_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    job_id = "job-file"
    d = tmp_path / job_id
    d.mkdir()
    app_module.persist_job_source_url(
        str(d), "https://www.youtube.com/watch?v=fromfile")
    assert (d / ".source_url").read_text().strip() == (
        "https://www.youtube.com/watch?v=fromfile")
    assert app_module.listed_source_url(job_id) == (
        "https://www.youtube.com/watch?v=fromfile")


def test_listed_source_url_prefers_cmd_then_metadata_then_file(
        tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    job_id = "job-order"
    d = tmp_path / job_id
    d.mkdir()
    app_module.persist_job_source_url(str(d), "https://example.com/file")
    meta = {"source_url": "https://example.com/meta"}
    rec = {"cmd": ["/usr/bin/python", "-u", "main.py", "-u",
                   "https://example.com/cmd"]}
    assert app_module.listed_source_url(
        job_id, rec=rec, meta=meta) == "https://example.com/cmd"
    assert app_module.listed_source_url(
        job_id, rec={}, meta=meta) == "https://example.com/meta"
    assert app_module.listed_source_url(
        job_id, rec={}, meta={}) == "https://example.com/file"


def test_listed_source_url_unreadable_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    assert app_module.listed_source_url("missing") is None


def test_persist_skips_blank_url(tmp_path):
    d = tmp_path / "job"
    d.mkdir()
    app_module.persist_job_source_url(str(d), "")
    app_module.persist_job_source_url(str(d), None)
    assert not (d / ".source_url").exists()


@pytest.mark.skipif(
    bool(os.environ.get("JOB_RETENTION_SECONDS")
         or os.environ.get("SOURCE_RETENTION_SECONDS")),
    reason="env overrides the defaults under test",
)
def test_self_host_retention_defaults():
    assert app_module.BILLING_ENABLED is False
    assert app_module.JOB_RETENTION_SECONDS == 604800
    assert app_module.SOURCE_RETENTION_SECONDS == 86400
    assert app_module.SOURCE_RETENTION_SECONDS < app_module.JOB_RETENTION_SECONDS


def _get(path):
    async def _do():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            return await client.get(path)
    return asyncio.run(_do())


def test_api_jobs_includes_source_url_from_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    job_id = "mem-url-job"
    (tmp_path / job_id).mkdir()
    app_module.jobs[job_id] = {
        "status": "completed",
        "logs": [],
        "cmd": ["/usr/bin/python", "-u", "main.py", "-u",
                "https://www.youtube.com/watch?v=mem", "--keep-original"],
        "result": {"clips": [{"title": "A"}]},
        "created_at": 1,
    }
    try:
        r = _get("/api/jobs")
        assert r.status_code == 200
        row = next(j for j in r.json()["jobs"] if j["job_id"] == job_id)
        assert row["source_url"] == "https://www.youtube.com/watch?v=mem"
        assert row["clips"] == 1
    finally:
        app_module.jobs.pop(job_id, None)


def test_api_jobs_disk_job_reads_metadata_then_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    job_id = "disk-url-job"
    d = tmp_path / job_id
    d.mkdir()
    (d / "Talk_Title_metadata.json").write_text(json.dumps({
        "source_video": "Talk_Title.mp4",
        "source_url": "https://www.youtube.com/watch?v=disk",
        "shorts": [{}, {}],
    }))
    r = _get("/api/jobs")
    row = next(j for j in r.json()["jobs"] if j["job_id"] == job_id)
    assert row["source_url"] == "https://www.youtube.com/watch?v=disk"
    assert row["name"] == "Talk_Title"
    assert row["clips"] == 2


def test_api_jobs_upload_has_null_source_url(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    job_id = "upload-job"
    app_module.jobs[job_id] = {
        "status": "completed",
        "logs": [],
        "cmd": ["/usr/bin/python", "-u", "main.py", "-i", "/tmp/a.mp4"],
        "result": {"clips": []},
        "created_at": 2,
    }
    (tmp_path / job_id).mkdir()
    try:
        r = _get("/api/jobs")
        row = next(j for j in r.json()["jobs"] if j["job_id"] == job_id)
        assert row["source_url"] is None
    finally:
        app_module.jobs.pop(job_id, None)
