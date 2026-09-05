"""Server-side timestamps for System Logs (option B).

The panel rendered new Date() at paint time, so every row showed the
latest poll time instead of when the line actually arrived. These tests
pin the two-layer contract: entries stored as {ts, msg} objects with a
single append helper, normalized back to plain strings for every legacy
consumer (cloud filter, error classifier), and the status endpoint
keeping `logs` as strings while adding `logs_v2` with real timestamps.
"""
import asyncio
import time

import httpx
import pytest

app_module = pytest.importorskip("app")


def _status(job_id):
    async def go():
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_module.app),
                base_url="http://t") as c:
            return await c.get(f"/api/status/{job_id}")
    return asyncio.run(go())


@pytest.fixture()
def job_id():
    jid = "ts-test-job"
    app_module.jobs[jid] = {
        "status": "processing",
        "logs": [],
        "user_id": None,
        "result": None,
    }
    yield jid
    app_module.jobs.pop(jid, None)


def test_append_log_stores_ts_and_msg(job_id):
    before = time.time()
    app_module._append_log(job_id, "hello")
    after = time.time()
    entry = app_module.jobs[job_id]["logs"][0]
    assert entry["msg"] == "hello"
    assert before <= entry["ts"] <= after


def test_log_messages_legacy_strings(job_id):
    assert app_module._log_messages(["a", "b"]) == ["a", "b"]


def test_log_messages_mixed_objects_and_strings(job_id):
    logs = [{"ts": 100.0, "msg": "a"}, "b", {"ts": None, "msg": "c"}]
    assert app_module._log_messages(logs) == ["a", "b", "c"]


def test_status_keeps_string_logs_and_adds_v2(job_id):
    app_module._append_log(job_id, "first")
    time.sleep(0.01)
    app_module._append_log(job_id, "second")
    r = _status(job_id)
    assert r.status_code == 200
    body = r.json()
    assert body["logs"] == ["first", "second"]
    v2 = body["logs_v2"]
    assert [e["msg"] for e in v2] == ["first", "second"]
    assert v2[0]["ts"] <= v2[1]["ts"]
    assert isinstance(v2[0]["ts"], (int, float))


def test_status_v2_survives_legacy_string_entries(job_id):
    app_module.jobs[job_id]["logs"] = ["legacy line"]
    r = _status(job_id)
    body = r.json()
    assert body["logs"] == ["legacy line"]
    assert body["logs_v2"] == [{"ts": None, "msg": "legacy line"}]


def test_error_text_accepts_object_entries():
    logs = [{"ts": 1.0, "msg": "plain line"},
            {"ts": 2.0, "msg": "❌ boom failed"}]
    assert "boom" in app_module._job_error_text(logs)


def test_visible_logs_accepts_object_entries(monkeypatch):
    monkeypatch.setattr(app_module, "BILLING_ENABLED", False)
    logs = [{"ts": 1.0, "msg": "raw line"}]
    assert app_module._visible_logs(logs) == ["raw line"]
