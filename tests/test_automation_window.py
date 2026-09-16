"""Jam operasional dan urutan pengurasan antrean (Tahap 5).

Menggantikan tes is_due() yang lama: penjadwalannya berubah dari "sekali sehari
jam run_hour" menjadi "kuras antrean satu per satu selama jam operasional".

Batas akhirnya EKSKLUSIF - window 8-15 berarti pekerjaan baru boleh mulai
sampai 14:59 - dan itu dikunci di sini karena seluruh keputusan "video yang
mulai 14:55 diselesaikan sampai tuntas" bergantung padanya.
"""
from datetime import datetime, timedelta, timezone

import pytest

import automation as A

WIB = timezone(timedelta(hours=7))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "AUTOMATION_DIR", str(tmp_path / "automation"))
    return A


def wib(jam, menit=30, hari=16):
    return datetime(2026, 9, hari, jam, menit, tzinfo=WIB)


def aktif(store, **ubah):
    setelan = {"enabled": True, "run_hour": 8, "run_hour_end": 15,
               "timezone": "Asia/Jakarta"}
    setelan.update(ubah)
    return store.save_settings(setelan)


# --- in_window ----------------------------------------------------------------

@pytest.mark.parametrize("jam", [8, 12, 14])
def test_di_dalam_window(store, jam):
    aktif(store)
    assert store.in_window(now=wib(jam)) is True


@pytest.mark.parametrize("jam", [0, 7, 15, 23])
def test_di_luar_window(store, jam):
    aktif(store)
    assert store.in_window(now=wib(jam)) is False


def test_batas_1459_lolos_1500_tidak(store):
    """Mengunci keputusan "mulai 14:55, butuh 30 menit -> lanjut"."""
    aktif(store)
    assert store.in_window(now=wib(14, 59)) is True
    assert store.in_window(now=wib(15, 0)) is False


def test_akhir_24_berarti_habis_hari(store):
    aktif(store, run_hour_end=24)
    assert store.in_window(now=wib(23)) is True


def test_ikut_timezone(store):
    aktif(store)
    # 01:00 UTC = 08:00 WIB
    assert store.in_window(now=datetime(2026, 9, 16, 1, 0,
                                        tzinfo=timezone.utc)) is True
    # 00:00 UTC = 07:00 WIB
    assert store.in_window(now=datetime(2026, 9, 16, 0, 0,
                                        tzinfo=timezone.utc)) is False


def test_setelan_lama_tanpa_run_hour_end_dianggap_habis_hari(store):
    store._write(store._SETTINGS, {"enabled": True, "run_hour": 8,
                                   "timezone": "Asia/Jakarta"})
    assert store.get_settings()["run_hour_end"] == 24
    assert store.in_window(now=wib(23)) is True


# --- validasi setelan ---------------------------------------------------------

def test_akhir_harus_setelah_mulai(store):
    with pytest.raises(ValueError):
        store.save_settings({"enabled": True, "run_hour": 8, "run_hour_end": 6})


def test_kosong_berarti_habis_hari(store):
    aktif(store)
    assert store.save_settings({"run_hour_end": ""})["run_hour_end"] == 24
    assert store.save_settings({"run_hour_end": None})["run_hour_end"] == 24


@pytest.mark.parametrize("buruk", [0, 25, -1, "x"])
def test_akhir_di_luar_rentang_ditolak(store, buruk):
    with pytest.raises(ValueError):
        store.save_settings({"run_hour_end": buruk})


def test_satu_permintaan_boleh_mengubah_keduanya(store):
    hasil = store.save_settings({"enabled": True, "run_hour": 9,
                                 "run_hour_end": 17})
    assert hasil["run_hour"] == 9 and hasil["run_hour_end"] == 17


def test_ubah_mulai_saja_sampai_menabrak_akhir_ditolak(store):
    aktif(store)
    with pytest.raises(ValueError):
        store.save_settings({"run_hour": 20})


def test_berkas_dengan_pasangan_mustahil_diperbaiki_bukan_dilempar(store):
    store._write(store._SETTINGS, {"enabled": True, "run_hour": 20,
                                   "run_hour_end": 6,
                                   "timezone": "Asia/Jakarta"})
    setelan = store.get_settings()          # tidak boleh melempar
    assert setelan["run_hour_end"] == 24


# --- next_to_start: urutan pengurasan antrean ---------------------------------

def test_ada_job_jalan_tidak_memulai_apa_pun(store):
    """Rem satu per satu: selama ada job jalan, tidak ada yang baru dimulai."""
    aktif(store)
    store.add_pending({"video_id": "v1"})
    store.mark_queued("v1", "job-1")
    store.add_pending({"video_id": "v2"})
    assert store.next_to_start(now=wib(10)) is None


def test_di_dalam_window_mengambil_yang_menunggu(store):
    aktif(store)
    store.add_pending({"video_id": "v1"})
    item = store.next_to_start(now=wib(10))
    assert item is not None and item["video_id"] == "v1"


def test_di_luar_window_tidak_mengambil_dan_tidak_menghilangkan(store):
    aktif(store)
    store.add_pending({"video_id": "v1"})
    assert store.next_to_start(now=wib(20)) is None
    assert store.find_pending("v1")["status"] == "new"


def test_retry_jalan_walau_di_luar_window(store):
    """Keputusan: retry tidak menunggu jam."""
    aktif(store)
    store.add_pending({"video_id": "v1"})
    store.mark_pending_failure("v1", "jaringan mati")      # -> retry
    # Jatuh temponya dipatok ke masa lalu supaya tesnya tidak bergantung pada
    # jam dinding saat suite dijalankan.
    store.update_pending("v1", next_attempt_at=0)
    item = store.next_to_start(now=wib(20))                # jam 20 WIB
    assert item is not None and item["video_id"] == "v1"


def test_retry_didahulukan_daripada_video_baru(store):
    aktif(store)
    store.add_pending({"video_id": "v1"})
    store.mark_pending_failure("v1", "jaringan mati")
    store.update_pending("v1", next_attempt_at=0)
    store.add_pending({"video_id": "v2"})
    assert store.next_to_start(now=wib(10))["video_id"] == "v1"


def test_retry_yang_belum_jatuh_tempo_tidak_diambil(store):
    aktif(store)
    store.add_pending({"video_id": "v1"})
    store.mark_pending_failure("v1", "jaringan mati")      # +30 menit
    assert store.next_to_start(now=wib(10)) is None


def test_yang_paling_lama_menunggu_diambil_dulu(store):
    aktif(store)
    store.add_pending({"video_id": "v1"})
    store.add_pending({"video_id": "v2"})
    assert store.next_to_start(now=wib(10))["video_id"] == "v1"


def test_pending_in_flight_hanya_menghitung_queued(store):
    aktif(store)
    for vid in ("v1", "v2", "v3"):
        store.add_pending({"video_id": vid})
    store.mark_queued("v1", "job-1")
    store.mark_job_finished("job-1", True)                 # v1 -> done
    store.mark_pending_failure("v3", "gagal")              # v3 -> retry
    assert store.pending_in_flight() == []
    store.mark_queued("v2", "job-2")
    assert [p["video_id"] for p in store.pending_in_flight()] == ["v2"]


def test_tanpa_video_tidak_ada_yang_dimulai(store):
    aktif(store)
    assert store.next_to_start(now=wib(10)) is None
