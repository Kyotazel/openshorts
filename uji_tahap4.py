"""Uji Tahap 4: notifikasi autopilot, tanpa memproses video YouTube.

KENAPA ADA: menguji Tahap 4 tanpa menunggu channel upload dan tanpa memotong
video. Yang diuji adalah dua hal yang paling mudah salah - isi pesannya, dan
sifat "keluar tepat sekali" - plus kesiapan setelan di mesin ini.

PAKAI:
    python uji_tahap4.py            # uji saja, tidak ada pesan dikirim
    python uji_tahap4.py --kirim    # sekalian kirim satu pesan contoh

Store-nya diarahkan ke direktori sementara, jadi file antrean asli
(automation/pending.json) TIDAK tersentuh.
"""
import argparse
import asyncio
import os
import shutil
import sys
import tempfile

from dotenv import load_dotenv

load_dotenv()

import automation as A           # noqa: E402
import autopilot_notify as N     # noqa: E402

HASIL = []


def catat(nama, lulus, keterangan=""):
    HASIL.append(bool(lulus))
    tanda = "OK   " if lulus else "GAGAL"
    print(f"  {tanda} {nama:46} {keterangan}")


def uji_pesan():
    print("=== 1. ISI PESAN ===")
    contoh = [
        ("📺 video baru", N.pesan_video_baru("Cara Berpikir Kritis",
                                             "Motivasi Kaya", "jam 08:00")),
        ("▶️ mulai", N.pesan_mulai("Cara Berpikir Kritis", 2)),
        ("✂️ selesai", N.pesan_clipping_selesai("Cara Berpikir Kritis", 5, 754)),
        ("⏭️ dilewati", N.pesan_dilewati("Video Pendek", "durasi 30 dtk")),
        ("❌ gagal", N.pesan_gagal_proses("Judul", "exit code 1")),
    ]
    for nama, teks in contoh:
        catat(f"{nama} ada penanda autopilot", "(autopilot)" in teks)
        catat(f"{nama} tanpa 'None'", "None" not in teks and bool(teks.strip()))

    panjang = [b for b in N.pesan_dilewati("J", "x" * 1000).split("\n")
               if b.startswith("x")][0]
    catat("alasan panjang dipotong 300", len(panjang) == 300, f"{len(panjang)} karakter")

    print()
    print("  Contoh yang akan masuk Telegram:")
    for baris in N.pesan_clipping_selesai("Cara Berpikir Kritis", 5, 754).split("\n"):
        print(f"    | {baris}")
    print()


def uji_penjaga(store):
    print("=== 2. PENJAGA ANTI-DOBEL ===")
    store.add_pending({"video_id": "uji-v1", "title": "Video Uji"})
    pertama = store.mark_queued("uji-v1", "job-1")
    kedua = store.mark_queued("uji-v1", "job-2")
    catat("mark_queued pertama menang", pertama is not None)
    catat("mark_queued kedua kalah", kedua is None, "-> tidak ada pesan dobel")
    catat("job_id tidak tertimpa", store.find_pending("uji-v1")["job_id"] == "job-1")

    store.add_pending({"video_id": "uji-v2", "title": "Pendek"})
    store.mark_pending_failure("uji-v2", "durasi 30 dtk", permanent=True)
    lagi = store.mark_pending_failure("uji-v2", "durasi 30 dtk", permanent=True)
    catat("'dilewati' hanya sekali", lagi is None)
    catat("status jadi skip", store.find_pending("uji-v2")["status"] == "skip")

    store.add_pending({"video_id": "uji-v3", "title": "Sementara"})
    store.mark_pending_failure("uji-v3", "jaringan mati")
    catat("gagal sementara jadi retry",
          store.find_pending("uji-v3")["status"] == "retry",
          "-> TIDAK dikabari 'dilewati'")

    store.add_pending({"video_id": "uji-v4", "title": "Selesai"})
    store.mark_queued("uji-v4", "job-4")
    store.mark_job_finished("job-4", True)
    catat("job selesai hanya dituntaskan sekali",
          store.mark_job_finished("job-4", True) is None)
    print()


def uji_setelan():
    print("=== 3. SETELAN DI MESIN INI ===")
    setelan = A.get_settings()
    delivery = setelan.get("delivery") or {}
    url = str(delivery.get("url") or "")
    catat("delivery URL terisi", bool(url), url or "(kosong)")
    catat("delivery secret terisi", bool(delivery.get("secret")),
          "ya" if delivery.get("secret") else "KOSONG (kirim akan ditolak 401)")
    if url.startswith("http://127.0.0.1") or url.startswith("http://localhost"):
        privat = os.environ.get("AUTOMATION_ALLOW_PRIVATE_TARGET", "").lower() in (
            "1", "true", "yes")
        catat("izin target lokal (AUTOMATION_ALLOW_PRIVATE_TARGET)", privat,
              "wajib untuk 127.0.0.1" if not privat else "ya")
    subs = A.list_subscriptions()
    catat("ada channel dipantau", len(subs) > 0,
          ", ".join(s.get("title") or s.get("channel_id", "") for s in subs)
          or "(belum ada - tambah di dashboard)")
    catat("notifikasi tidak dimatikan", N.aktif(),
          "TELEGRAM_DISABLED aktif!" if not N.aktif() else "ya")
    catat("TELEGRAM_BOT_TOKEN ada", bool(os.environ.get("TELEGRAM_BOT_TOKEN")))
    catat("TELEGRAM_CHAT_ID ada", bool(os.environ.get("TELEGRAM_CHAT_ID")))
    print()


def kirim_contoh():
    print("=== 4. KIRIM PESAN CONTOH ===")
    teks = ("🧪 Uji Tahap 4\n\n"
            "Kalau pesan ini masuk, jalur notifikasi autopilot sudah tersambung.\n"
            "Pesan sungguhan akan berawalan 📺 ▶️ ✂️ 📦 ⏭️ atau ❌.")
    hasil = asyncio.run(N.kirim(teks))
    catat("pesan contoh terkirim", hasil)
    print()


def main() -> int:
    p = argparse.ArgumentParser(description="Uji Tahap 4 (notifikasi autopilot)")
    p.add_argument("--kirim", action="store_true",
                   help="kirim satu pesan contoh sungguhan ke Telegram")
    args = p.parse_args()

    print()
    print("UJI TAHAP 4 - notifikasi autopilot")
    print("=" * 56)
    print()

    asli = A.AUTOMATION_DIR
    sementara = tempfile.mkdtemp(prefix="uji-tahap4-")
    A.AUTOMATION_DIR = sementara
    try:
        uji_pesan()
        uji_penjaga(A)
    finally:
        A.AUTOMATION_DIR = asli
        shutil.rmtree(sementara, ignore_errors=True)

    uji_setelan()
    if args.kirim:
        kirim_contoh()
    else:
        print("=== 4. KIRIM PESAN CONTOH ===")
        print("  (dilewati - jalankan dengan --kirim untuk mencoba sungguhan)")
        print()

    lulus = sum(1 for h in HASIL if h)
    gagal = len(HASIL) - lulus
    print("=" * 56)
    if gagal:
        print(f"{lulus} lulus, {gagal} GAGAL")
        return 1
    print(f"SEMUA LULUS ({lulus} pemeriksaan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
