"""Agent uploads (POST/PUT /api/uploads -> process with upload_id) and the
per-job captions switch, through the MCP transport where the agent uses them."""
import asyncio
import os
import shutil
import subprocess

import httpx
import pytest

app_module = pytest.importorskip("app")


def _client():
    transport = httpx.ASGITransport(app=app_module.app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver",
                             headers={"X-Gemini-Key": "test-key"})


def _mcp(tool, arguments):
    async def _do():
        async with _client() as c:
            return await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                              "params": {"name": tool, "arguments": arguments}})
    return asyncio.run(_do())


def _tool_payload(resp):
    import json
    return json.loads(resp.json()["result"]["content"][0]["text"])


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    out_root = tmp_path / "output"; up_root = tmp_path / "uploads"
    out_root.mkdir(); up_root.mkdir()
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(out_root))
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(up_root))
    # Never spawn main.py: capture the job instead.
    captured = {}
    async def fake_enqueue(*a, **k):
        captured["args"] = a; captured["kwargs"] = k
    monkeypatch.setattr(app_module, "_media_duration_seconds", lambda p: 120.0)
    return out_root, up_root, captured


def test_create_upload_is_an_mcp_tool():
    names = [t["name"] for t in __import__("mcp_server").TOOLS]
    assert "create_upload" in names
    pv = next(t for t in __import__("mcp_server").TOOLS if t["name"] == "process_video")
    assert "upload_id" in pv["inputSchema"]["properties"]
    assert "captions" in pv["inputSchema"]["properties"]
    assert "source_url" not in pv["inputSchema"]["required"]


def test_process_video_needs_a_source():
    payload = _tool_payload(_mcp("process_video", {"confirm_rights": True}))
    assert "source_url" in payload["error"] and "upload_id" in payload["error"]


def test_put_then_process_with_upload_id_and_captions_off(dirs, monkeypatch):
    out_root, up_root, _ = dirs
    slot = _tool_payload(_mcp("create_upload", {"filename": "../talk.mp4"}))
    assert slot["upload_url"].endswith(f"/api/uploads/{slot['upload_id']}")
    assert "talk.mp4" in app_module.pending_uploads[slot["upload_id"]]["path"]
    assert ".." not in os.path.basename(app_module.pending_uploads[slot["upload_id"]]["path"])

    async def _put(body):
        async with _client() as c:
            return await c.put(f"/api/uploads/{slot['upload_id']}", content=body)
    assert asyncio.run(_put(b"")).status_code == 400
    resp = asyncio.run(_put(b"\x00" * 5000))
    assert resp.status_code == 200 and resp.json()["bytes"] == 5000

    # Intercept the job instead of running main.py.
    started = {}
    async def fake_put(item):
        started["job"] = item
    monkeypatch.setattr(app_module.job_queue, "put", fake_put)

    payload = _tool_payload(_mcp("process_video", {
        "upload_id": slot["upload_id"], "confirm_rights": True, "captions": False}))
    assert "job_id" in payload, payload
    job = app_module.jobs[payload["job_id"]]
    assert job["env"]["AUTO_CAPTIONS"] == "0"
    assert slot["upload_id"] not in app_module.pending_uploads
    input_path = job["cmd"][job["cmd"].index("-i") + 1]
    assert os.path.exists(input_path) and input_path.startswith(str(up_root))


def test_captions_default_leaves_env_alone(dirs, monkeypatch):
    slot = _tool_payload(_mcp("create_upload", {}))
    async def _put():
        async with _client() as c:
            return await c.put(f"/api/uploads/{slot['upload_id']}", content=b"x" * 100)
    assert asyncio.run(_put()).status_code == 200
    async def fake_put(item): pass
    monkeypatch.setattr(app_module.job_queue, "put", fake_put)
    payload = _tool_payload(_mcp("process_video", {"upload_id": slot["upload_id"], "confirm_rights": True}))
    assert "AUTO_CAPTIONS" not in app_module.jobs[payload["job_id"]]["env"]


def test_unknown_or_unfinished_upload_id(dirs):
    async def _put():
        async with _client() as c:
            return await c.put("/api/uploads/nope", content=b"x")
    assert asyncio.run(_put()).status_code == 404
    slot = _tool_payload(_mcp("create_upload", {}))
    payload = _tool_payload(_mcp("process_video", {"upload_id": slot["upload_id"], "confirm_rights": True}))
    assert "PUT" in payload["error"]
