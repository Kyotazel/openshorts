"""Notifikasi Telegram untuk jalur AUTOPILOT (channel YouTube -> ZIP).

BEDA DARI manual_notify.py: di sana setiap pesan bisa dimatikan per-job lewat
centang di form Clip Generator. Autopilot tidak punya centang - kalau
autopilotnya menyala, notifikasinya ikut menyala. Menyatukan keduanya membuat
alur manual tenggelam di antara kode autopilot, persis masalah yang modul itu
dibuat untuk menghindari.

DUA ATURAN yang sama seperti notifikasi lain di repo ini:
  1. Tidak pernah melempar - notifikasi gagal tidak boleh menggagalkan job.
  2. Kalau TELEGRAM_DISABLED aktif, diam saja.

Setiap header diberi penanda "(autopilot)" supaya bisa dibedakan dari pesan job
manual, yang memakai emoji sama.
"""
import os


def aktif() -> bool:
    """Notifikasi dimatikan lewat env yang sama dengan cloud/alerts."""
    return os.environ.get("TELEGRAM_DISABLED", "").strip().lower() not in (
        "1", "true", "yes")


async def kirim(teks: str) -> bool:
    """Kirim satu pesan; False kalau tidak terkirim. Tidak pernah melempar."""
    if not aktif():
        return False
    try:
        from cloud import alerts
        await alerts.send_telegram(teks)
        return True
    except Exception as e:
        # Termasuk kegagalan impor: self-host mungkin tidak memuat cloud/.
        print(f"Notifikasi autopilot gagal: {e}")
        return False


def judul_dari(item, klip=None) -> str:
    """Judul untuk pesan.

    Menerima item antrean (kunci "title") maupun klip hasil (kunci
    "video_title_for_youtube_short"). Klip diutamakan karena judulnya sudah
    versi pendek yang dipakai untuk YouTube Shorts.
    """
    for sumber in (klip, item):
        if not isinstance(sumber, dict):
            continue
        for kunci in ("video_title_for_youtube_short", "title"):
            nilai = str(sumber.get(kunci) or "").strip()
            if nilai:
                return nilai
    return ""


def _kutip(judul: str, pengganti: str) -> str:
    return f'"{judul}"' if judul else pengganti


def pesan_video_baru(judul: str, channel: str = "", jadwal: str = "") -> str:
    baris = ["📺 Video baru terdeteksi (autopilot)", ""]
    baris.append(_kutip(judul, "Video baru"))
    if channel:
        baris.append(f"dari {channel}")
    baris += ["", f"Masuk antrean. Diproses {jadwal}." if jadwal
              else "Masuk antrean, menunggu jadwal harian."]
    return "\n".join(baris)


def pesan_mulai(judul: str, sisa_antrean: int = 0) -> str:
    baris = ["▶️ Mulai memproses (autopilot)", ""]
    baris.append(_kutip(judul, "Video"))
    if sisa_antrean > 0:
        baris.append(f"{sisa_antrean} video lain menyusul")
    baris += ["", "Mengunduh, transkrip, lalu mencari momen viral."]
    return "\n".join(baris)


def pesan_clipping_selesai(judul: str, jumlah: int, durasi_detik: float = 0) -> str:
    baris = ["✂️ Clipping selesai (autopilot)", ""]
    baris.append(_kutip(judul, "Video"))
    baris.append(f"{jumlah} klip siap")
    if durasi_detik:
        menit = int(durasi_detik // 60)
        detik = int(durasi_detik % 60)
        baris.append(f"total {menit} menit {detik} detik")
    baris += ["", "Menyiapkan ZIP untuk Klip-Studio..."]
    return "\n".join(baris)


def pesan_dilewati(judul: str, alasan: str) -> str:
    """HANYA untuk kegagalan permanen, bukan yang masih akan dicoba lagi.

    Video yang masih di antrean retry bukan "dilewati" - kalau dikabari, chat
    kebanjiran pesan yang sama sampai tiga kali.
    """
    baris = ["⏭️ Video dilewati (autopilot)", ""]
    if judul:
        baris += [_kutip(judul, ""), ""]
    baris.append(str(alasan or "sebab tidak diketahui")[:300])
    baris += ["", "Video ini tidak akan dicoba lagi.",
              "Bisa dijalankan ulang dari dashboard kalau perlu."]
    return "\n".join(baris)


def pesan_gagal_proses(judul: str, sebab: str,
                       akan_dicoba_lagi: bool = True) -> str:
    baris = ["❌ Gagal memproses (autopilot)", ""]
    if judul:
        baris += [_kutip(judul, ""), ""]
    baris.append(str(sebab or "sebab tidak diketahui")[:300])
    baris.append("")
    baris.append("Akan dicoba lagi otomatis." if akan_dicoba_lagi
                 else "Sudah dicoba 3x dan menyerah. Bisa dijalankan ulang "
                      "dari dashboard.")
    return "\n".join(baris)


# Isinya sudah generik dan tidak menyebut "manual" sama sekali, jadi dipakai
# ulang daripada diduplikasi. Diimpor di sini supaya app.py cukup mengimpor
# satu modul notifikasi autopilot.
from manual_notify import pesan_terkirim, pesan_gagal_kirim  # noqa: E402,F401
