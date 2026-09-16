"""HTTP + wiring tests for the autopilot: channels, WebSub, submit, outbox.

Same conventions as test_source_trim_api.py: a real ASGI round-trip against the
imported app (BILLING_ENABLED=0 via conftest), with the store pointed at tmp
and YouTube replaced by stubs. The worker never runs under ASGITransport, so
submission is asserted at the job-record/manifest level.
"""
import asyncio
import json
import os

import httpx
import pytest

app_module = pytest.importorskip("app")
automation = app_module.automation
channel_watch = app_module.channel_watch

CID = "UC" + "c" * 22


def _req(method, path, **kwargs):
    async def _do():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(_do())


def _atom(video_id="abc123", published="2030-01-01T00:00:00+00:00", channel=CID):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        'xmlns="http://www.w3.org/2005/Atom">'
        "<title>Channel</title><entry>"
        f"<yt:videoId>{video_id}</yt:videoId>"
        f"<yt:channelId>{channel}</yt:channelId>"
        "<title>New video</title>"
        f'<link rel="alternate" href="https://www.youtube.com/watch?v={video_id}"/>'
        f"<published>{published}</published>"
        "</entry></feed>")


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    out_root = tmp_path / "output"
    up_root = tmp_path / "uploads"
    out_root.mkdir()
    up_root.mkdir()
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(out_root))
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(up_root))
    monkeypatch.setattr(automation, "AUTOMATION_DIR", str(tmp_path / "automation"))
    app_module.jobs.clear()
    yield out_root, up_root
    app_module.jobs.clear()


@pytest.fixture()
def no_spawn(monkeypatch):
    """Swallow spawned tasks so ASGITransport tests stay deterministic."""
    calls = []

    def _capture(coro):
        calls.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(app_module, "_automation_spawn", _capture)
    return calls


def _stub_probe(monkeypatch, duration=600, max_height=1080):
    async def _probe(url):
        return {"max_height": max_height, "duration": duration}
    monkeypatch.setattr(app_module, "_probe_youtube_quality", _probe)


# --- settings + status ---------------------------------------------------------

def test_status_defaults(dirs):
    body = _req("GET", "/api/automation").json()
    assert body["settings"]["enabled"] is False
    assert body["settings"]["run_at"] == "08:00"
    assert body["settings"]["run_until"] == ""
    assert body["settings"]["delivery"]["secret"] == ""
    assert body["secret_set"] is False
    assert body["subscriptions"] == [] and body["pending"] == []


def test_settings_roundtrip_and_secret_never_returned(dirs):
    resp = _req("POST", "/api/automation/settings", json={
        "enabled": True, "run_at": "09:15",
        "delivery": {"url": "https://api.test/hook", "secret": "shh",
                     "headers": [{"name": "X-Key", "value": "v"}]}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["run_at"] == "09:15"
    assert body["settings"]["delivery"]["secret"] == ""
    assert body["secret_set"] is True
    # Updating the hour must not wipe the stored secret.
    resp2 = _req("POST", "/api/automation/settings", json={"run_at": "10:30"})
    assert resp2.json()["secret_set"] is True
    assert automation.get_settings()["delivery"]["secret"] == "shh"


def test_settings_rejects_bad_values(dirs):
    assert _req("POST", "/api/automation/settings",
                json={"run_at": "99:00"}).status_code == 400
    assert _req("POST", "/api/automation/settings",
                json={"timezone": "Nowhere/Here"}).status_code == 400


# --- channels ------------------------------------------------------------------

def test_add_list_remove_channel(dirs, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_URL", "https://app.test")

    def _resolve(query, client=None):
        return {"channel_id": CID, "title": "My Channel", "handle": "@mine"}
    monkeypatch.setattr(channel_watch, "resolve", _resolve)
    monkeypatch.setattr(channel_watch, "websub_subscribe",
                        lambda cb, cid, client=None, lease_seconds=0: True)
    monkeypatch.setattr(channel_watch, "websub_unsubscribe",
                        lambda cb, cid, client=None: True)

    resp = _req("POST", "/api/automation/channels", json={"query": "@mine"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True and body["websub"] is True
    sub = body["subscription"]
    assert sub["channel_id"] == CID and sub["title"] == "My Channel"
    assert sub["lease_expires_at"]

    again = _req("POST", "/api/automation/channels", json={"query": "@mine"})
    assert again.json()["created"] is False

    listed = _req("GET", "/api/automation").json()
    assert len(listed["subscriptions"]) == 1

    removed = _req("DELETE", f"/api/automation/channels/{sub['id']}")
    assert removed.status_code == 200
    assert _req("GET", "/api/automation").json()["subscriptions"] == []


def test_published_after_allows_a_same_day_upload(dirs):
    """Tanggal dari tab channel hanya perkiraan, jadi toleransinya sehari.

    Tanpa ini, video yang diupload pagi hari (dibulatkan ke tengah malam)
    akan ditolak oleh langganan yang dibuat siang harinya.
    """
    assert app_module._published_after(
        "2026-09-16T00:00:00+00:00", "2026-09-16T06:24:33+00:00") is True


def test_published_after_still_rejects_an_old_archive(dirs):
    """Yang dicegah: arsip lama ikut masuk saat channel baru ditambahkan."""
    assert app_module._published_after(
        "2024-01-01T00:00:00+00:00", "2026-09-16T06:24:33+00:00") is False
    assert app_module._published_after(None, "2026-09-16T06:24:33+00:00") is False


def test_add_channel_without_public_url_is_rss_only(dirs, monkeypatch):
    monkeypatch.delenv("PUBLIC_API_URL", raising=False)
    monkeypatch.setattr(channel_watch, "resolve",
                        lambda query, client=None: {"channel_id": CID, "title": "C",
                                                    "handle": ""})
    body = _req("POST", "/api/automation/channels", json={"query": "x"}).json()
    assert body["websub"] is False
    assert body["subscription"]["status"] == "rss-only"


def test_add_channel_rejects_unresolvable(dirs, monkeypatch):
    def _boom(query, client=None):
        raise ValueError("Could not find a channel")
    monkeypatch.setattr(channel_watch, "resolve", _boom)
    assert _req("POST", "/api/automation/channels",
                json={"query": "nope"}).status_code == 400


# --- WebSub callbacks ----------------------------------------------------------

def test_websub_verification_echoes_challenge_and_stores_lease(dirs):
    sub = automation.add_subscription(CID, title="C")
    resp = _req("GET", "/api/automation/youtube/callback", params={
        "hub.mode": "subscribe", "hub.topic": channel_watch.topic_url(CID),
        "hub.challenge": "challenge-123", "hub.lease_seconds": "3600"})
    assert resp.status_code == 200 and resp.text == "challenge-123"
    stored = automation.find_subscription(sub_id=sub["id"])
    assert stored["status"] == "active" and stored["lease_expires_at"]


def test_websub_verification_without_challenge_is_400(dirs):
    assert _req("GET", "/api/automation/youtube/callback").status_code == 400


def test_websub_push_parks_only_new_videos(dirs):
    automation.add_subscription(CID, title="Channel")
    resp = _req("POST", "/api/automation/youtube/callback",
                content=_atom("vid-new").encode(),
                headers={"content-type": "application/atom+xml"})
    assert resp.status_code == 200 and resp.json()["added"] == 1
    item = automation.find_pending("vid-new")
    assert item["status"] == "new" and item["channel_title"] == "Channel"

    old = _req("POST", "/api/automation/youtube/callback",
               content=_atom("vid-old", published="2000-01-01T00:00:00+00:00").encode(),
               headers={"content-type": "application/atom+xml"})
    assert old.json()["added"] == 0 and automation.find_pending("vid-old") is None


def test_websub_push_for_unknown_channel_is_ignored(dirs):
    resp = _req("POST", "/api/automation/youtube/callback",
                content=_atom("vid-x", channel="UC" + "z" * 22).encode(),
                headers={"content-type": "application/atom+xml"})
    assert resp.json()["added"] == 0


def test_websub_denied_is_absorbed(dirs):
    resp = _req("POST", "/api/automation/youtube/callback?hub.mode=denied",
                content=b"", headers={"content-type": "application/xml"})
    assert resp.status_code == 200 and resp.json()["ok"] is True


# --- submit + outbox -----------------------------------------------------------

def test_build_job_marks_automation_and_writes_manifest(dirs, monkeypatch):
    _stub_probe(monkeypatch)
    automation.add_subscription(CID, title="Channel")
    item, _ = automation.add_pending({"video_id": "v1", "channel_id": CID,
                                      "title": "T", "channel_title": "Channel",
                                      "url": "https://www.youtube.com/watch?v=v1"})
    result = asyncio.run(app_module._automation_build_job(item))
    job_id = result["job_id"]
    record = app_module.jobs[job_id]
    assert record["automation"]["video_id"] == "v1"
    assert record["status"] == "queued"
    manifest = json.loads(open(
        os.path.join(app_module.OUTPUT_DIR, job_id, ".resume.json")).read())
    assert manifest["automation"]["video_id"] == "v1"
    assert "https://www.youtube.com/watch?v=v1" in record["cmd"]
    assert record["cmd"][-2:] == ["-o", str(app_module.OUTPUT_DIR + "/" + job_id)]


def test_build_job_retries_a_low_quality_upload(dirs, monkeypatch):
    _stub_probe(monkeypatch, max_height=360)
    automation.add_pending({"video_id": "v1", "url": "https://youtu.be/v1"})
    item = automation.find_pending("v1")
    with pytest.raises(app_module.AutomationSourceError) as err:
        asyncio.run(app_module._automation_build_job(item))
    assert err.value.permanent is False


def test_build_job_permanently_skips_a_short_source(dirs, monkeypatch):
    _stub_probe(monkeypatch, duration=10)
    automation.add_pending({"video_id": "v1", "url": "https://youtu.be/v1"})
    item = automation.find_pending("v1")
    with pytest.raises(app_module.AutomationSourceError) as err:
        asyncio.run(app_module._automation_build_job(item))
    assert err.value.permanent is True


def test_after_job_writes_outbox_and_marks_done(dirs, monkeypatch, no_spawn):
    out_root, _ = dirs
    job_id = "job-auto-1"
    job_dir = out_root / job_id
    job_dir.mkdir()
    (job_dir / "src_clip_1.mp4").write_bytes(b"clip")
    (job_dir / "src_metadata.json").write_text(json.dumps({
        "shorts": [{"title": "Clip", "video_description_for_tiktok": "TT"}]}))
    app_module.jobs[job_id] = {
        "status": "completed",
        "output_dir": str(job_dir),
        "result": {"clips": [{"video_url": f"/videos/{job_id}/src_clip_1.mp4"}]},
        "automation": {"video_id": "v1", "channel_title": "Channel",
                       "source_url": "https://youtu.be/v1"},
    }
    automation.add_pending({"video_id": "v1"})
    automation.mark_queued("v1", job_id)
    asyncio.run(app_module._automation_after_job(job_id))

    assert automation.find_pending("v1")["status"] == "done"
    state = automation.outbox_get(job_id)
    assert state["status"] == "pending" and state["meta"]["clip_count"] == 1
    assert len(no_spawn) == 1  # the send itself was spawned, not awaited


def test_after_job_marks_a_failed_run_for_retry(dirs, monkeypatch, no_spawn):
    job_id = "job-auto-2"
    app_module.jobs[job_id] = {
        "status": "failed",
        "output_dir": str(dirs[0] / job_id),
        "logs": ["Process failed with exit code 1"],
        "automation": {"video_id": "v2"},
    }
    automation.add_pending({"video_id": "v2"})
    automation.mark_queued("v2", job_id)
    asyncio.run(app_module._automation_after_job(job_id))
    item = automation.find_pending("v2")
    assert item["status"] == "retry" and item["last_error"]


def test_after_job_ignores_manual_jobs(dirs, no_spawn):
    app_module.jobs["job-manual"] = {"status": "completed", "result": {"clips": [{}]}}
    asyncio.run(app_module._automation_after_job("job-manual"))
    assert automation.outbox_list() == [] and no_spawn == []


def test_send_success_marks_sent(dirs, monkeypatch):
    async def _ok(url, zip_path, **kwargs):
        return True, ""
    monkeypatch.setattr(app_module.automation_delivery, "post_zip", _ok)
    monkeypatch.setattr(app_module, "_automation_prepare_zip",
                        lambda job_id, state: ("/tmp/fake.zip", None))
    automation.outbox_put("job-1", {
        "job_id": "job-1", "status": "pending", "attempts": 0, "next_attempt_at": 0,
        "target": {"url": "https://api.test/hook"}, "meta": {}})
    state = automation.outbox_get("job-1")
    assert asyncio.run(app_module._automation_send("job-1", state)) is True
    assert automation.outbox_get("job-1")["status"] == "sent"


def test_send_failure_ladder_ends_failed(dirs, monkeypatch):
    async def _fail(url, zip_path, **kwargs):
        return False, "HTTP 500: boom"
    monkeypatch.setattr(app_module.automation_delivery, "post_zip", _fail)
    monkeypatch.setattr(app_module, "_automation_prepare_zip",
                        lambda job_id, state: ("/tmp/fake.zip", None))
    automation.outbox_put("job-2", {
        "job_id": "job-2", "status": "pending", "attempts": 0, "next_attempt_at": 0,
        "target": {"url": "https://api.test/hook"}, "meta": {}})
    for _ in range(3):
        automation.outbox_put("job-2", {**automation.outbox_get("job-2"),
                                        "next_attempt_at": 0})
        asyncio.run(app_module._automation_send("job-2",
                                                automation.outbox_get("job-2")))
    state = automation.outbox_get("job-2")
    assert state["status"] == "failed" and state["attempts"] == 3
    assert "boom" in state["last_error"]


def test_manual_delivery_retry_resets_state(dirs, no_spawn):
    automation.outbox_put("job-3", {
        "job_id": "job-3", "status": "failed", "attempts": 3,
        "next_attempt_at": 0, "last_error": "boom", "meta": {}})
    resp = _req("POST", "/api/automation/deliveries/job-3/retry")
    assert resp.status_code == 200
    state = automation.outbox_get("job-3")
    assert state["status"] == "pending" and state["attempts"] == 0
    assert len(no_spawn) == 1


def test_manual_pending_retry_resets_and_submits(dirs, no_spawn):
    automation.add_pending({"video_id": "v1", "url": "https://youtu.be/v1"})
    automation.update_pending("v1", status="failed", attempts=3)
    resp = _req("POST", "/api/automation/pending/v1/retry")
    assert resp.status_code == 200
    assert automation.find_pending("v1")["status"] == "new"
    assert len(no_spawn) == 1


def test_run_now_reports_pending_count(dirs, no_spawn):
    automation.add_pending({"video_id": "v1"})
    body = _req("POST", "/api/automation/run-now").json()
    assert body["pending"] == 1 and body["status"] == "started"


def test_daily_pass_marks_today_and_submits(dirs, monkeypatch):
    submitted = []

    async def _submit(item):
        submitted.append(item["video_id"])
    monkeypatch.setattr(app_module, "_automation_submit_pending", _submit)
    automation.add_pending({"video_id": "v1"})
    automation.add_pending({"video_id": "v2"})
    asyncio.run(app_module._automation_daily_pass())
    assert sorted(submitted) == ["v1", "v2"]
    assert automation.get_schedule()["last_run_date"] == \
        automation.local_now().date().isoformat()


# --- jam operasional (Tahap 5) -------------------------------------------------

def _window_lebar(dirs):
    """Window yang pasti melingkupi jam berapa pun, supaya tesnya tidak
    bergantung pada jam dinding saat suite dijalankan."""
    return _req("POST", "/api/automation/settings",
                json={"enabled": True, "run_at": "00:00", "run_until": ""})


def test_jam_operasional_lewat_api(dirs):
    r = _req("POST", "/api/automation/settings",
             json={"enabled": True, "run_at": "08:45", "run_until": "15:30"})
    assert r.status_code == 200
    assert r.json()["settings"]["run_at"] == "08:45"
    assert r.json()["settings"]["run_until"] == "15:30"
    diambil = _req("GET", "/api/automation").json()["settings"]
    assert diambil["run_at"] == "08:45" and diambil["run_until"] == "15:30"


def test_run_until_kosong_berarti_habis_hari(dirs):
    r = _req("POST", "/api/automation/settings",
             json={"enabled": True, "run_at": "08:45", "run_until": ""})
    assert r.status_code == 200
    assert r.json()["settings"]["run_until"] == ""


def test_run_until_lebih_awal_ditolak_dengan_pesan_jelas(dirs):
    r = _req("POST", "/api/automation/settings",
             json={"enabled": True, "run_at": "08:00", "run_until": "06:00"})
    assert r.status_code == 400
    assert "must be later than" in r.json()["detail"]


def test_jam_ngawur_ditolak_dengan_pesan_jelas(dirs):
    r = _req("POST", "/api/automation/settings", json={"run_at": "25:00"})
    assert r.status_code == 400
    assert "08:45" in r.json()["detail"]


def test_pump_memulai_satu_video(dirs, monkeypatch):
    _window_lebar(dirs)
    automation.add_pending({"video_id": "v1"})
    dipanggil = []

    async def _catat(item):
        dipanggil.append(item)

    monkeypatch.setattr(app_module, "_automation_submit_pending", _catat)
    asyncio.run(app_module._automation_pump())
    assert [i["video_id"] for i in dipanggil] == ["v1"]


def test_pump_tidak_memulai_job_kedua(dirs, monkeypatch):
    """Rem satu per satu - beban server terbatas pada satu Chromium."""
    _window_lebar(dirs)
    automation.add_pending({"video_id": "v1"})
    automation.mark_queued("v1", "job-1")
    automation.add_pending({"video_id": "v2"})
    dipanggil = []

    async def _catat(item):
        dipanggil.append(item)

    monkeypatch.setattr(app_module, "_automation_submit_pending", _catat)
    asyncio.run(app_module._automation_pump())
    assert dipanggil == []


def test_pump_tidak_memulai_di_luar_jam(dirs, monkeypatch):
    # Window 00:00-00:01 hampir pasti sudah lewat saat suite dijalankan.
    _req("POST", "/api/automation/settings",
         json={"enabled": True, "run_at": "00:00", "run_until": "00:01"})
    automation.add_pending({"video_id": "v1"})
    dipanggil = []

    async def _catat(item):
        dipanggil.append(item)

    monkeypatch.setattr(app_module, "_automation_submit_pending", _catat)
    asyncio.run(app_module._automation_pump())
    assert dipanggil == []
    assert automation.find_pending("v1")["status"] == "new"   # tidak hilang


def test_run_now_tetap_jalan_di_luar_jam(dirs, no_spawn):
    """Keputusan: run-now harus bisa dipakai kapan saja."""
    _req("POST", "/api/automation/settings",
         json={"enabled": True, "run_at": "00:00", "run_until": "00:01"})
    r = _req("POST", "/api/automation/run-now")
    assert r.status_code == 200
    assert len(no_spawn) == 1     # pass-nya tetap dijadwalkan
