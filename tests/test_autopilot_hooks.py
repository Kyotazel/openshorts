"""Pemasangan notifikasi ke alur autopilot: keluar tepat SEKALI di titik benar.

Yang diuji di sini bukan isi pesannya (itu di test_autopilot_notify.py),
melainkan sifat "sekali". Fungsi-fungsi ini memang bisa dipanggil dua kali -
pass harian dan retry sweep sama-sama memanggil _automation_submit_pending,
dan run-now dari API bisa menyusul - jadi "sekali" harus dibuktikan, bukan
diasumsikan.
"""
import asyncio

import pytest

app_module = pytest.importorskip("app")
automation = app_module.automation
autopilot_notify = app_module.autopilot_notify


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
def pesan(monkeypatch):
    """Rekam pesan alih-alih mengirimnya ke Telegram."""
    rekaman = []

    async def _rekam(teks):
        rekaman.append(teks)
        return True

    monkeypatch.setattr(autopilot_notify, "kirim", _rekam)
    return rekaman


@pytest.fixture()
def no_spawn(monkeypatch):
    def _capture(coro):
        coro.close()
        return None
    monkeypatch.setattr(app_module, "_automation_spawn", _capture)


def _probe(duration=600, max_height=1080):
    async def _p(_url):
        return {"duration": duration, "max_height": max_height}
    return _p


# --- 1. video baru -------------------------------------------------------------

def test_video_baru_dikabari_sekali(dirs, pesan):
    entry = {"video_id": "v1", "title": "Judul", "channel_title": "Chan"}
    asyncio.run(app_module._automation_add_pending_notified(entry))
    asyncio.run(app_module._automation_add_pending_notified(entry))
    assert len(pesan) == 1
    assert "Video baru" in pesan[0]
    assert "Chan" in pesan[0]


# --- 2. mulai ------------------------------------------------------------------

def test_mulai_dikabari_sekali_walau_submit_dipanggil_dua_kali(dirs, pesan, monkeypatch):
    automation.add_pending({"video_id": "v1", "title": "Judul"})
    monkeypatch.setattr(app_module, "_probe_youtube_quality", _probe())
    monkeypatch.setattr(app_module, "_finalize_job", lambda **kw: {"job_id": "job-1"})
    item = automation.find_pending("v1")
    asyncio.run(app_module._automation_submit_pending(item))
    asyncio.run(app_module._automation_submit_pending(item))
    assert len(pesan) == 1
    assert "Mulai memproses" in pesan[0]
    assert automation.find_pending("v1")["job_id"] == "job-1"


# --- 5. dilewati ---------------------------------------------------------------

def test_video_terlalu_pendek_dikabari_sekali(dirs, pesan, monkeypatch):
    monkeypatch.setattr(app_module, "MIN_SOURCE_SECONDS", 45)
    automation.add_pending({"video_id": "v1", "title": "Terlalu pendek"})
    monkeypatch.setattr(app_module, "_probe_youtube_quality", _probe(duration=30))
    item = automation.find_pending("v1")
    asyncio.run(app_module._automation_submit_pending(item))
    asyncio.run(app_module._automation_submit_pending(item))
    assert len(pesan) == 1
    assert "dilewati" in pesan[0]
    assert automation.find_pending("v1")["status"] == "skip"


def test_kegagalan_sementara_tidak_dikabari(dirs, pesan, monkeypatch):
    """Video yang masih akan dicoba lagi BUKAN dilewati."""
    automation.add_pending({"video_id": "v1", "title": "J"})

    async def _gagal(_url):
        raise RuntimeError("jaringan mati")

    monkeypatch.setattr(app_module, "_automation_build_job", _gagal)
    asyncio.run(app_module._automation_submit_pending(automation.find_pending("v1")))
    assert pesan == []
    assert automation.find_pending("v1")["status"] == "retry"


# --- 3 dan 6. selesai dan gagal ------------------------------------------------

def _job_selesai(dirs, job_id="job-done", video_id="v1"):
    job_dir = dirs[0] / job_id
    job_dir.mkdir()
    app_module.jobs[job_id] = {
        "status": "completed",
        "output_dir": str(job_dir),
        "result": {"clips": [{"title": "Klip"}]},
        "automation": {"video_id": video_id, "title": "Judul"},
    }
    automation.add_pending({"video_id": video_id, "title": "Judul"})
    automation.mark_queued(video_id, job_id)
    return job_id


def test_clipping_selesai_dikabari_sekali(dirs, pesan, no_spawn):
    job_id = _job_selesai(dirs)
    asyncio.run(app_module._automation_after_job(job_id))
    asyncio.run(app_module._automation_after_job(job_id))
    assert len(pesan) == 1
    assert "Clipping selesai" in pesan[0]


def test_gagal_proses_dikabari_sekali(dirs, pesan, no_spawn):
    job_id = "job-gagal"
    (dirs[0] / job_id).mkdir()
    app_module.jobs[job_id] = {
        "status": "failed",
        "output_dir": str(dirs[0] / job_id),
        "logs": ["Process failed with exit code 1"],
        "automation": {"video_id": "v2", "title": "Judul"},
    }
    automation.add_pending({"video_id": "v2", "title": "Judul"})
    automation.mark_queued("v2", job_id)
    asyncio.run(app_module._automation_after_job(job_id))
    asyncio.run(app_module._automation_after_job(job_id))
    assert len(pesan) == 1
    assert "Gagal memproses" in pesan[0]
    assert automation.find_pending("v2")["status"] == "retry"


def test_job_manual_tidak_dikabari(dirs, pesan, no_spawn):
    app_module.jobs["job-manual"] = {
        "status": "completed", "result": {"clips": [{}]}}
    asyncio.run(app_module._automation_after_job("job-manual"))
    assert pesan == []


# --- 4. terkirim ---------------------------------------------------------------

def test_terkirim_dikabari_sekali(dirs, pesan, monkeypatch):
    job_id = "job-send"
    zip_path = automation.outbox_zip_path(job_id)
    monkeypatch.setattr(app_module, "_automation_prepare_zip",
                        lambda _job, _state: (zip_path, None))
    monkeypatch.setattr(app_module.automation_delivery, "post_zip",
                        lambda *a, **kw: _ok())
    state = {"job_id": job_id, "attempts": 0, "status": "pending",
             "target": {"url": "https://api.test/hook"},
             "meta": {"clip_count": 4}}
    automation.outbox_put(job_id, state)
    assert asyncio.run(app_module._automation_send(job_id, state)) is True
    assert len(pesan) == 1
    assert "Terkirim ke Klip-Studio" in pesan[0]
    # Panggilan kedua: outbox sudah "sent", jadi tidak boleh ada pesan baru.
    asyncio.run(app_module._automation_send(job_id, automation.outbox_get(job_id)))
    assert len(pesan) == 1


async def _ok():
    return True, ""
