"""Notifikasi Telegram untuk job MANUAL (dipilih di form Clip Generator).

KENAPA MODUL SENDIRI: autopilot dan manual punya siklus hidup berbeda.
Autopilot mengabari saat ZIP masuk antrean; manual mengabari saat job
diproses, selesai, lalu (kalau diminta) terkirim ke Klip-Studio. Menaruh
keduanya di app.py membuat alur manual tenggelam di antara kode autopilot.

DUA ATURAN yang sama seperti notifikasi lain di repo ini:
  1. Tidak pernah melempar - notifikasi gagal tidak boleh menggagalkan job.
  2. Kalau TELEGRAM_DISABLED aktif, diam saja.
"""
import os


def _aktif() -> bool:
    """Notifikasi dimatikan lewat env yang sama dengan cloud/alerts."""
    return os.environ.get("TELEGRAM_DISABLED", "").strip().lower() not in (
        "1", "true", "yes")


def diminta(nilai) -> bool:
    """Apakah pengguna mencentang opsi ini di form?

    Menerima "1"/"true"/"yes" karena form mengirim teks, dan None (tidak
    dikirim sama sekali) berarti TIDAK dicentang.
    """
    return str(nilai).strip().lower() in ("1", "true", "yes")


async def kirim(teks: str) -> bool:
    """Kirim satu pesan; False kalau tidak terkirim. Tidak pernah melempar."""
    if not _aktif():
        print("Notifikasi job manual dimatikan (TELEGRAM_DISABLED)")
        return False
    try:
        from cloud import alerts
        await alerts.send_telegram(teks)
        return True
    except Exception as e:
        # Termasuk kegagalan impor: self-host mungkin tidak memuat cloud/.
        print(f"Notifikasi job manual gagal: {e}")
        return False


def ringkas_judul(clips) -> str:
    """Judul video untuk pesan, dari klip pertama kalau ada."""
    for c in clips or []:
        judul = (c or {}).get("video_title_for_youtube_short") or (c or {}).get("title")
        if judul:
            return str(judul).strip()
    return ""


def pesan_mulai(judul: str, durasi_menit: float = 0) -> str:
    baris = ["▶️ Mulai memproses", ""]
    baris.append(f"\"{judul}\"" if judul else "Video baru")
    if durasi_menit:
        # Menjawab "kenapa lama?" sebelum ditanya.
        baris.append(f"Sumber {durasi_menit:.0f} menit")
    baris += ["", "Mengunduh, transkrip, lalu mencari momen viral."]
    return "\n".join(baris)


def pesan_selesai(judul: str, jumlah: int, durasi_detik: float, auto_send: bool) -> str:
    baris = ["✂️ Clip selesai", ""]
    baris.append(f"\"{judul}\"" if judul else "Video")
    menit = int(durasi_detik // 60)
    detik = int(durasi_detik % 60)
    baris.append(f"{jumlah} klip siap · total {menit} menit {detik} detik")
    baris.append("")
    if auto_send:
        baris.append("Mengirim ke Klip-Studio...")
    else:
        # Dua baris: baris pertama menyebut APA yang harus dilakukan, kedua
        # di mana melakukannya. Satu baris panjang terbaca seperti perintah.
        baris.append("Lihat di dashboard, lalu kirim ke Klip-Studio")
        baris.append("kalau sudah cocok.")
    return "\n".join(baris)


def pesan_terkirim(jumlah: int, ukuran_byte: int) -> str:
    mb = ukuran_byte / 1e6 if ukuran_byte else 0
    baris = ["📦 Terkirim ke Klip-Studio", ""]
    baris.append(f"{jumlah} klip" + (f" · ZIP {mb:.0f} MB" if mb else ""))
    baris += ["", "Klip-Studio akan render & publish.",
              "Tunggu kabar dari KLIP 🎬."]
    return "\n".join(baris)


def pesan_gagal(judul: str, sebab: str) -> str:
    baris = ["❌ Gagal memproses", ""]
    if judul:
        baris += [f"\"{judul}\"", ""]
    baris.append(sebab or "sebab tidak diketahui")
    return "\n".join(baris)


def pesan_gagal_kirim(sebab: str) -> str:
    return "\n".join([
        "⚠️ Gagal kirim ke Klip-Studio",
        "",
        str(sebab or "sebab tidak diketahui")[:300],
        "",
        "Klip tetap tersimpan di server. Bisa dikirim ulang dari History.",
    ])
