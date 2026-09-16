"""Hapus video sumber YouTube begitu ZIP-nya terkirim ke Klip-Studio.

Keputusan pemiliknya: sumbernya saja yang dihapus, klipnya dibiarkan. Jadi tes
ini sekaligus mengunci bahwa klip TIDAK ikut terhapus - itu bagian yang paling
mudah salah kalau nanti ada yang "merapikan" pembersihannya.
"""
import asyncio
import json

import pytest

app_module = pytest.importorskip("app")
automation = app_module.automation
autopilot_notify = app_module.autopilot_notify


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    out_root = tmp_path / "output"
    out_root.mkdir()
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(out_root))
    monkeypatch.setattr(automation, "AUTOMATION_DIR", str(tmp_path / "automation"))
    app_module.jobs.clear()
    yield out_root
    app_module.jobs.clear()


def _job_dengan_sumber(dirs, job_id="job-1", nama="sumber"):
    """Direktori job seperti aslinya: sumber + klip + metadata."""
    d = dirs / job_id
    d.mkdir(parents=True, exist_ok=True)
    sumber = d / f"{nama}.mp4"
    sumber.write_bytes(b"s" * 5000)
    klip = d / f"{nama}_clip_1.mp4"
    klip.write_bytes(b"k" * 500)
    (d / f"{nama}_metadata.json").write_text(
        json.dumps({"source_video": sumber.name, "shorts": []}))
    return sumber, klip


# --- penghapusan ---------------------------------------------------------------

def test_hapus_sumber_tidak_menyentuh_klip(dirs):
    sumber, klip = _job_dengan_sumber(dirs)
    assert app_module._hapus_sumber_job("job-1") is True
    assert not sumber.exists()
    assert klip.exists(), "klipnya harus dibiarkan"


def test_hapus_sumber_aman_dipanggil_berkali_kali(dirs):
    """Percobaan kirim ulang tidak boleh membuat ini melempar."""
    _job_dengan_sumber(dirs)
    assert app_module._hapus_sumber_job("job-1") is True
    assert app_module._hapus_sumber_job("job-1") is False


def test_tanpa_metadata_atau_tanpa_job_tidak_melempar(dirs):
    (dirs / "job-2").mkdir()
    assert app_module._hapus_sumber_job("job-2") is False
    assert app_module._hapus_sumber_job("tidak-ada-sama-sekali") is False


def test_metadata_tanpa_source_video_tidak_melempar(dirs):
    d = dirs / "job-3"
    d.mkdir()
    (d / "x_metadata.json").write_text(json.dumps({"shorts": []}))
    assert app_module._hapus_sumber_job("job-3") is False


# --- dipanggil pada saat yang benar --------------------------------------------

def test_sumber_dihapus_setelah_terkirim(dirs, monkeypatch):
    sumber, klip = _job_dengan_sumber(dirs)
    zip_path = automation.outbox_zip_path("job-1")
    monkeypatch.setattr(app_module, "_automation_prepare_zip",
                        lambda _job, _state: (zip_path, None))

    async def _post(*_a, **_kw):
        return True, ""

    async def _kirim(_teks):
        return True

    monkeypatch.setattr(app_module.automation_delivery, "post_zip", _post)
    monkeypatch.setattr(autopilot_notify, "kirim", _kirim)

    state = {"job_id": "job-1", "attempts": 0, "status": "pending",
             "target": {"url": "https://api.test/hook"},
             "meta": {"clip_count": 2}}
    automation.outbox_put("job-1", state)
    assert asyncio.run(app_module._automation_send("job-1", state)) is True
    assert not sumber.exists()
    assert klip.exists()


def test_sumber_tidak_dihapus_kalau_pengiriman_gagal(dirs, monkeypatch):
    """Gagal kirim berarti ZIP-nya belum sampai - jangan buang apa pun."""
    sumber, _klip = _job_dengan_sumber(dirs)
    monkeypatch.setattr(app_module, "_automation_prepare_zip",
                        lambda _job, _state: (None, "gagal menyiapkan"))

    state = {"job_id": "job-1", "attempts": 0, "status": "pending",
             "target": {"url": "https://api.test/hook"}, "meta": {}}
    automation.outbox_put("job-1", state)
    asyncio.run(app_module._automation_send("job-1", state))
    assert sumber.exists()


# --- link di pesan -------------------------------------------------------------

def test_pesan_video_baru_memuat_link_youtube():
    teks = autopilot_notify.pesan_video_baru(
        "Judul", "Chan", "jam 08:45", "https://www.youtube.com/watch?v=abc123")
    assert "https://www.youtube.com/watch?v=abc123" in teks
    assert "Judul" in teks


def test_pesan_video_baru_tanpa_link_tetap_aman():
    teks = autopilot_notify.pesan_video_baru("Judul")
    assert "youtube.com" not in teks
    assert "Judul" in teks
