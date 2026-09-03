"""HTTP tests for source_start/source_end on /api/process."""
import asyncio

import httpx
import pytest

app_module = pytest.importorskip("app")


def _post(json_body):
    async def _do():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            return await client.post(
                "/api/process", json=json_body,
                headers={"X-Gemini-Key": "test-key"},
            )
    return asyncio.run(_do())


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    out_root = tmp_path / "output"
    up_root = tmp_path / "uploads"
    out_root.mkdir()
    up_root.mkdir()
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(out_root))
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(up_root))
    return out_root, up_root


def _stub_probe(monkeypatch, duration=600, max_height=1080):
    async def _probe(url):
        return {"max_height": max_height, "duration": duration}
    monkeypatch.setattr(app_module, "_probe_youtube_quality", _probe)


def test_one_sided_window_is_400(dirs, monkeypatch):
    _stub_probe(monkeypatch)
    resp = _post({"url": "https://www.youtube.com/watch?v=ok",
                  "acknowledged": True, "source_start": 10})
    assert resp.status_code == 400


def test_inverted_window_is_400(dirs, monkeypatch):
    _stub_probe(monkeypatch)
    resp = _post({"url": "https://www.youtube.com/watch?v=ok",
                  "acknowledged": True, "source_start": 20, "source_end": 10})
    assert resp.status_code == 400


def test_window_past_duration_is_400(dirs, monkeypatch):
    _stub_probe(monkeypatch, duration=100)
    resp = _post({"url": "https://www.youtube.com/watch?v=ok",
                  "acknowledged": True, "source_start": 10, "source_end": 200})
    assert resp.status_code == 400
    assert "100" in resp.json()["detail"]
