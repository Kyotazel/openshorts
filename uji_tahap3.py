"""Uji Jalur A Tahap 3: kirim ZIP manual ke Klip-Studio + uji keamanan.

KENAPA ADA: menguji Tahap 3 tanpa memproses video YouTube. Memakai ZIP asli
yang sudah ada di disk, jadi yang diuji benar-benar jalur kirimnya.

PAKAI:
    python uji_tahap3.py            # jalankan semua uji
    python uji_tahap3.py --kirim    # sekalian kirim ZIP sungguhan

Yang diuji:
  1. Tanpa login              -> harus 401
  2. Login                    -> harus 200
  3. Signature SALAH          -> harus 401
  4. TANPA header signature   -> harus 401
  5. Signature BENAR          -> harus 202
  6. Notifikasi Telegram      -> pesan "Batch diterima" masuk
"""
import argparse, hashlib, hmac, os, sys
import httpx
from dotenv import load_dotenv

load_dotenv()

KLIP = os.environ.get("UJI_KLIP_URL", "http://127.0.0.1:3000")
USER = os.environ.get("UJI_APP_USER", os.environ.get("APP_USER", "ordo"))
PASS = os.environ.get("UJI_APP_PASSWORD", os.environ.get("APP_PASSWORD", "secret123"))
SECRET = os.environ.get("UJI_SECRET", "")
ZIP_PATH = os.environ.get("UJI_ZIP", "/Users/oktaariaditya/ordo/Auto Clipping/klip-opencut/apps/web/.klip-data/batches/b_f15d8db78844.zip")


def main() -> int:
    if not os.path.exists(ZIP_PATH):
        print(f"ZIP tidak ada: {ZIP_PATH}"); return 1
    data = open(ZIP_PATH, "rb").read()
    print(f"ZIP: {os.path.basename(ZIP_PATH)} ({len(data):,} byte)")
    print(f"Server: {KLIP}")
    print(f"Secret: {'diisi' if SECRET else 'KOSONG (verifikasi signature mati)'}")
    print()

    sig_ok = "sha256=" + hmac.new(SECRET.encode(), data, hashlib.sha256).hexdigest() if SECRET else None
    hasil = []

    def catat(nama, dapat, harus):
        lulus = dapat == harus
        hasil.append(lulus)
        print(f"  {'OK   ' if lulus else 'GAGAL'} {nama:42} {dapat} (harus {harus})")

    with httpx.Client(base_url=KLIP, timeout=120, follow_redirects=False) as c:
        def upload(headers):
            return c.post("/api/klip/batches",
                          files={"file": ("uji-tahap3.zip", data, "application/zip")},
                          headers=headers)

        print("=== UJI KEAMANAN ===")
        h = {"X-OpenShorts-Signature": sig_ok} if sig_ok else {}
        catat("1. tanpa login", upload(h).status_code, 401)

        r = c.post("/api/auth/login", json={"username": USER, "password": PASS})
        catat("2. login", r.status_code, 200)
        if r.status_code != 200:
            print(f"\n  Login gagal: {r.text[:200]}")
            print("  Periksa APP_USER / APP_PASSWORD di .env.local klip-opencut")
            return 1

        if SECRET:
            catat("3. signature SALAH",
                  upload({"X-OpenShorts-Signature": "sha256=" + "0" * 64}).status_code, 401)
            catat("4. TANPA header signature", upload({}).status_code, 401)
            catat("5. signature BENAR", upload({"X-OpenShorts-Signature": sig_ok}).status_code, 202)
        else:
            print("  (3-5 dilewati: KLIP_WEBHOOK_SECRET belum diisi di klip-opencut)")
            catat("5. kirim tanpa signature (mode dev)", upload({}).status_code, 202)

    print()
    lulus = all(hasil)
    print("SEMUA LULUS" if lulus else f"{hasil.count(False)} GAGAL")
    print()
    print("Cek Telegram: pesan \"KLIP \U0001F3AC \u00b7 \U0001F4E5 Batch diterima\" harus masuk.")
    return 0 if lulus else 1


if __name__ == "__main__":
    raise SystemExit(main())
