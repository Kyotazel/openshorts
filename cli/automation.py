"""Kirim manual ke Klip-Studio: lihat antrian, kirim ZIP, bikin ZIP saja.

KENAPA ADA: alur otomatis seharusnya tidak perlu manusia, tapi saat MENGUJI
kita justru butuh kendali penuh - kirim satu video tertentu, lihat apa yang
menunggu, atau bikin ZIP-nya saja tanpa mengirim. Tanpa alat ini, satu-satunya
cara menguji adalah menunggu jadwal dan berharap tidak ada yang salah.

PAKAI:
    python -m cli.automation status              # antrian + outbox
    python -m cli.automation build-zip <job_id>  # bikin ZIP, TIDAK kirim
    python -m cli.automation kirim <job_id>      # kirim satu ZIP
    python -m cli.automation kirim-semua         # kirim semua yang tertunda

GATE TETAP BERLAKU: perintah di sini TIDAK melewati pemeriksaan apa pun -
signature HMAC, penjaga SSRF, dan batas 3 percobaan kirim semuanya tetap jalan.
Yang dilewati hanya PENJADWALAN (tidak menunggu jam), itu memang tujuannya.
"""
import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import automation  # noqa: E402
import automation_delivery  # noqa: E402


def _jam(ts) -> str:
    """Epoch -> jam lokal yang bisa dibaca, atau "-" kalau kosong."""
    if not ts:
        return "-"
    try:
        from datetime import datetime
        return datetime.fromtimestamp(float(ts)).strftime("%d %b %H:%M")
    except Exception:
        return "-"


def cmd_status(_args) -> int:
    settings = automation.get_settings()
    subs = automation.list_subscriptions()
    pending = automation.list_pending()
    outbox = automation.outbox_list()
    delivery = settings.get("delivery") or {}

    print("=== SETELAN ===")
    print(f"  autopilot   : {'AKTIF' if settings.get('enabled') else 'mati'}")
    print(f"  jam proses  : {settings.get('run_hour')}:00 ({settings.get('timezone')})")
    url = delivery.get("url") or ""
    print(f"  tujuan kirim: {url or '(BELUM DIISI - kirim akan gagal)'}")
    print(f"  signature   : {'ya' if delivery.get('secret') else 'tidak'}")

    print(f"\n=== CHANNEL ({len(subs)}) ===")
    if not subs:
        print("  (belum ada - tambah lewat dashboard)")
    for s in subs:
        print(f"  {s.get('title') or s.get('channel_id')}  [{s.get('status')}]")

    print(f"\n=== ANTRIAN VIDEO ({len(pending)}) ===")
    if not pending:
        print("  (kosong)")
    for p in pending[:20]:
        print(f"  [{p.get('status'):7}] {p.get('title') or p.get('video_id')}"
              f"  (job: {p.get('job_id') or '-'})")
        if p.get("last_error"):
            print(f"            error: {str(p['last_error'])[:80]}")

    print(f"\n=== OUTBOX ({len(outbox)}) ===")
    if not outbox:
        print("  (kosong)")
    for o in outbox[:20]:
        job_id = o.get("job_id")
        ada = os.path.exists(automation.outbox_zip_path(job_id)) if job_id else False
        print(f"  [{o.get('status'):8}] {job_id}  percobaan {o.get('attempts', 0)}"
              f"  ZIP {'ada' if ada else 'HILANG'}")
        if o.get("last_error"):
            print(f"             error: {str(o['last_error'])[:80]}")
    return 0


def cmd_build_zip(args) -> int:
    """Bikin ZIP saja. Dipakai untuk memeriksa isinya sebelum dikirim."""
    zip_path = automation.outbox_zip_path(args.job_id)
    if os.path.exists(zip_path) and os.path.getsize(zip_path) > 0:
        print(f"ZIP sudah ada: {zip_path} ({os.path.getsize(zip_path)} byte)")
        _cetak_isi(zip_path)
        return 0
    state = automation.outbox_get(args.job_id)
    if not state:
        print(f"Tidak ada catatan outbox untuk job {args.job_id}.", file=sys.stderr)
        print("Jalankan 'status' untuk melihat job yang ada.", file=sys.stderr)
        return 1
    # Memakai jalur yang SAMA dengan pengiriman sungguhan, supaya ZIP yang
    # diperiksa di sini benar-benar ZIP yang akan dikirim.
    from app import _automation_prepare_zip  # impor lokal: app.py berat
    path, error = _automation_prepare_zip(args.job_id, state)
    if error:
        print(f"Gagal membuat ZIP: {error}", file=sys.stderr)
        return 1
    print(f"ZIP dibuat: {path} ({os.path.getsize(path)} byte)")
    _cetak_isi(path)
    return 0


def _cetak_isi(zip_path: str) -> None:
    import zipfile
    print("\nIsi ZIP:")
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            print(f"  {n}")
        # captions.json adalah alasan ZIP ini ada; ketiadaannya berarti caption
        # per video tidak akan terpakai, dan itu lebih baik ketahuan sekarang.
        if "captions.json" in z.namelist():
            print("\ncaptions.json: ADA")
        else:
            print("\ncaptions.json: TIDAK ADA (caption per video tidak akan terpakai)")


def _kirim(job_id: str) -> bool:
    state = automation.outbox_get(job_id)
    if not state:
        print(f"Tidak ada catatan outbox untuk job {job_id}.", file=sys.stderr)
        return False
    # Reset supaya pengiriman manual tidak dihitung sebagai percobaan yang
    # sudah lewat batas - pengguna yang memaksa kirim berarti percobaan baru.
    state.update(status="pending", attempts=0, next_attempt_at=0, last_error=None)
    automation.outbox_put(job_id, state)
    from app import _automation_send
    return asyncio.run(_automation_send(job_id, state))


def cmd_kirim(args) -> int:
    ok = _kirim(args.job_id)
    if ok:
        print(f"Terkirim: {args.job_id}")
        return 0
    print(f"GAGAL kirim: {args.job_id} (lihat \"status\" untuk pesan errornya)",
          file=sys.stderr)
    return 1


def cmd_kirim_semua(_args) -> int:
    due = [o for o in automation.outbox_list() if o.get("status") != "sent"]
    if not due:
        print("Tidak ada yang perlu dikirim.")
        return 0
    print(f"Mengirim {len(due)} ZIP...")
    gagal = 0
    for o in due:
        job_id = o.get("job_id")
        if _kirim(job_id):
            print(f"  OK    {job_id}")
        else:
            print(f"  GAGAL {job_id}")
            gagal += 1
    print(f"\nSelesai: {len(due) - gagal} terkirim, {gagal} gagal.")
    return 1 if gagal else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cli.automation",
        description="Kirim manual ke Klip-Studio (gate tetap berlaku)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="lihat antrian, outbox, dan setelan")

    p = sub.add_parser("build-zip", help="bikin ZIP saja, tidak kirim")
    p.add_argument("job_id")

    p = sub.add_parser("kirim", help="kirim satu ZIP ke Klip-Studio")
    p.add_argument("job_id")

    sub.add_parser("kirim-semua", help="kirim semua ZIP yang belum terkirim")

    args = parser.parse_args()
    return {
        "status": cmd_status,
        "build-zip": cmd_build_zip,
        "kirim": cmd_kirim,
        "kirim-semua": cmd_kirim_semua,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
