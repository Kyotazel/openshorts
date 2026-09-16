"""Pesan notifikasi autopilot dan penjaga anti-duplikatnya.

Tes mark_queued adalah inti pencegahan spam. Tanpa compare-and-set, satu video
bisa mengirim "Mulai memproses" dua kali: pass harian mengambil item berstatus
new, retry sweep mengambil yang berstatus retry, dan keduanya berjalan di loop
yang sama sementara run-now dari API bisa menyusul.
"""
import asyncio

import pytest

import automation as A
import autopilot_notify as N


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "AUTOMATION_DIR", str(tmp_path / "automation"))
    return A


# --- isi pesan -----------------------------------------------------------------

def test_video_baru_memuat_judul_channel_dan_jadwal():
    teks = N.pesan_video_baru("Judul", "Motivasi Kaya", "jam 08:00")
    assert "📺" in teks
    assert "Judul" in teks
    assert "Motivasi Kaya" in teks
    assert "jam 08:00" in teks


def test_tanpa_jadwal_ada_teks_pengganti():
    teks = N.pesan_video_baru("Judul")
    assert "menunggu jadwal harian" in teks
    assert "None" not in teks


def test_setiap_pesan_punya_penanda_autopilot():
    """Supaya bisa dibedakan dari pesan job manual, yang emojinya sama."""
    for teks in (N.pesan_video_baru("J"), N.pesan_mulai("J"),
                 N.pesan_clipping_selesai("J", 3), N.pesan_dilewati("J", "S"),
                 N.pesan_gagal_proses("J", "S")):
        assert "(autopilot)" in teks


def test_semua_pesan_aman_tanpa_judul():
    for teks in (N.pesan_video_baru(""), N.pesan_mulai(""),
                 N.pesan_clipping_selesai("", 0), N.pesan_dilewati("", ""),
                 N.pesan_gagal_proses("", "")):
        assert teks.strip()
        assert "None" not in teks


def test_alasan_dipotong_300_karakter():
    baris = N.pesan_dilewati("J", "x" * 1000).split("\n")
    isi = [b for b in baris if b.startswith("x")][0]
    assert len(isi) == 300


def test_pesan_gagal_proses_membedakan_masih_akan_dicoba():
    assert "dicoba lagi otomatis" in N.pesan_gagal_proses("J", "S")
    assert "menyerah" in N.pesan_gagal_proses("J", "S", akan_dicoba_lagi=False)


def test_judul_dari_mengutamakan_klip():
    item = {"title": "Judul Item"}
    klip = {"video_title_for_youtube_short": "Judul Klip"}
    assert N.judul_dari(item, klip) == "Judul Klip"
    assert N.judul_dari(item, None) == "Judul Item"
    assert N.judul_dari(item, {}) == "Judul Item"
    assert N.judul_dari({}, {}) == ""
    assert N.judul_dari(None, None) == ""


# --- pengiriman ----------------------------------------------------------------

def test_kirim_diam_saat_telegram_dimatikan(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DISABLED", "1")
    assert asyncio.run(N.kirim("halo")) is False


def test_kirim_memanggil_send_telegram(monkeypatch):
    monkeypatch.delenv("TELEGRAM_DISABLED", raising=False)
    from cloud import alerts
    dipanggil = []

    async def rekam(teks):
        dipanggil.append(teks)

    monkeypatch.setattr(alerts, "send_telegram", rekam)
    assert asyncio.run(N.kirim("halo")) is True
    assert dipanggil == ["halo"]


def test_kirim_tidak_melempar_saat_pengiriman_gagal(monkeypatch):
    monkeypatch.delenv("TELEGRAM_DISABLED", raising=False)
    from cloud import alerts

    async def boom(_teks):
        raise RuntimeError("jaringan mati")

    monkeypatch.setattr(alerts, "send_telegram", boom)
    assert asyncio.run(N.kirim("halo")) is False


# --- penjaga anti-duplikat -----------------------------------------------------

def test_claim_pending_hanya_berhasil_kalau_status_cocok(store):
    store.add_pending({"video_id": "v1", "title": "A"})
    assert store.claim_pending("v1", "queued", status="queued") is None
    assert store.find_pending("v1")["status"] == "new"
    hasil = store.claim_pending("v1", "new", status="queued", job_id="j1")
    assert hasil["status"] == "queued" and hasil["job_id"] == "j1"


def test_claim_pending_mengembalikan_none_untuk_video_tak_dikenal(store):
    assert store.claim_pending("tidak-ada", "new", status="queued") is None


def test_mark_queued_hanya_menang_sekali(store):
    """Inti pencegahan spam: notifikasi mulai menempel pada kemenangan ini."""
    store.add_pending({"video_id": "v1"})
    assert store.mark_queued("v1", "j1") is not None
    assert store.mark_queued("v1", "j2") is None
    assert store.find_pending("v1")["job_id"] == "j1"


def test_mark_queued_menerima_item_yang_sedang_retry(store):
    store.add_pending({"video_id": "v1"})
    store.mark_pending_failure("v1", "360p only")
    assert store.find_pending("v1")["status"] == "retry"
    assert store.mark_queued("v1", "j1") is not None
    assert store.find_pending("v1")["status"] == "queued"


def test_mark_queued_tidak_menyentuh_yang_sudah_selesai(store):
    store.add_pending({"video_id": "v1"})
    store.mark_queued("v1", "j1")
    store.mark_job_finished("j1", True)
    assert store.find_pending("v1")["status"] == "done"
    assert store.mark_queued("v1", "j2") is None
    assert store.find_pending("v1")["status"] == "done"
