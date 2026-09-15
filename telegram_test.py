"""Tes koneksi Telegram untuk OpenShorts.

KENAPA ADA: key yang salah baru ketahuan saat batch asli jalan. Perintah ini
memastikan token dan chat_id benar SEBELUM ada pekerjaan yang dipertaruhkan.

PAKAI:
    python telegram_test.py            # kirim pesan tes
    python telegram_test.py --status   # cek konfigurasi saja

Catatan: diletakkan di root, BUKAN di cli/ - folder itu bukan Python package
(tidak ada __init__.py) dan isinya CLI publik dari repo upstream.
"""
import asyncio
import sys

from dotenv import load_dotenv

# Sama seperti app.py: .env harus dibaca SEBELUM cloud.config diimpor, karena
# config membaca os.environ saat dipanggil dan tidak memuat .env sendiri.
load_dotenv()

from cloud import alerts  # noqa: E402
from cloud.config import settings  # noqa: E402


async def main() -> int:
    token = settings.telegram_bot_token
    chat = settings.telegram_chat_id

    print(f"TELEGRAM_BOT_TOKEN : {'terisi (' + str(len(token)) + ' karakter)' if token else 'KOSONG'}")
    print(f"TELEGRAM_CHAT_ID   : {chat or 'KOSONG'}")
    print(f"terkonfigurasi     : {settings.telegram_configured}")

    if not settings.telegram_configured:
        print()
        print("Belum dikonfigurasi. Isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID")
        print("di .env, lalu jalankan lagi.")
        print()
        print("Catatan: ini BUKAN error - aplikasi tetap jalan tanpa Telegram.")
        return 0

    if "--status" in sys.argv:
        return 0

    # raise_errors=True di sini SENGAJA: untuk perintah tes, kegagalan harus
    # terlihat sebagai exit code 1, bukan ditelan diam-diam seperti di runtime.
    await alerts.send_telegram("Tes koneksi berhasil \u2705", raise_errors=True)
    print()
    print("\u2705 Terkirim - cek Telegram.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as e:
        print(f"\n\u274C Gagal kirim: {type(e).__name__}: {e}")
        raise SystemExit(1)
