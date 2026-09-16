"""Autopilot store (self-host): channels, pending videos, schedule, outbox.

File-based because self-host has no database. Everything lives under
AUTOMATION_DIR (default ./automation) -- deliberately NOT under output/, whose
janitor deletes job directories by mtime and would eat the queue, the schedule
and any ZIP still waiting to be delivered.

Every write is atomic (a .tmp file plus os.replace) and serialised by one
re-entrant lock, so a PM2 restart halfway through a write leaves either the old
file or the new one, never half of each. A file that cannot be parsed is moved
aside (.corrupt-<ts>) and rebuilt from defaults instead of taking the service
down.
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# Module-level so tests can point it at a tmp dir (the rest of the repo does the
# same with its tunables). Everything reads through _dir().
AUTOMATION_DIR = os.environ.get("AUTOMATION_DIR", "automation")
DEFAULT_TIMEZONE = "Asia/Jakarta"
DEFAULT_RUN_HOUR = 8
# 24 berarti "sampai habis hari". Dipakai alih-alih 23 supaya jam 23:00-23:59
# ikut masuk window; di dashboard ditampilkan sebagai "23:59 (habis hari)".
DEFAULT_RUN_HOUR_END = 24

_SETTINGS = "settings.json"
_SUBSCRIPTIONS = "subscriptions.json"
_PENDING = "pending.json"
_SCHEDULE = "schedule.json"
_OUTBOX_DIR = "outbox"

DEFAULT_SETTINGS = {
    "enabled": False,
    "run_hour": DEFAULT_RUN_HOUR,
    "run_hour_end": DEFAULT_RUN_HOUR_END,
    "timezone": DEFAULT_TIMEZONE,
    "delivery": {
        "url": "",
        "headers": [],          # [{"name": "...", "value": "..."}]
        "secret": "",
        "file_field": "file",
    },
}

_lock = threading.RLock()


def _dir() -> str:
    return os.path.abspath(AUTOMATION_DIR)


def outbox_dir() -> str:
    return os.path.join(_dir(), _OUTBOX_DIR)


def ensure():
    os.makedirs(outbox_dir(), exist_ok=True)


def _path(name: str) -> str:
    return os.path.join(_dir(), name)


def _read(name: str, default):
    path = _path(name)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return json.loads(json.dumps(default))
    except Exception as e:
        # A half-written or hand-edited file must not take the service down on
        # startup: keep the evidence, start clean.
        aside = f"{path}.corrupt-{int(time.time())}"
        try:
            os.replace(path, aside)
            print(f"WARNING: automation: unreadable {name} moved to {aside}: {e}")
        except Exception:
            print(f"WARNING: automation: unreadable {name}: {e}")
        return json.loads(json.dumps(default))


def _write(name: str, data):
    with _lock:
        ensure()
        path = _path(name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _zone(name):
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


# --- settings -----------------------------------------------------------------

def get_settings() -> dict:
    data = _read(_SETTINGS, DEFAULT_SETTINGS)
    if not isinstance(data, dict):
        data = {}
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    for key in ("enabled", "run_hour", "run_hour_end", "timezone"):
        if key in data:
            merged[key] = data[key]
    delivery = data.get("delivery")
    if isinstance(delivery, dict):
        merged["delivery"].update(
            {k: v for k, v in delivery.items() if k in merged["delivery"]})
    try:
        merged["run_hour"] = int(merged["run_hour"])
    except (TypeError, ValueError):
        merged["run_hour"] = DEFAULT_RUN_HOUR
    merged["run_hour"] = min(23, max(0, merged["run_hour"]))
    try:
        merged["run_hour_end"] = int(merged["run_hour_end"])
    except (TypeError, ValueError):
        merged["run_hour_end"] = DEFAULT_RUN_HOUR_END
    else:
        merged["run_hour_end"] = min(24, max(1, merged["run_hour_end"]))
    if merged["run_hour_end"] <= merged["run_hour"]:
        # Berkas lama, atau yang disunting tangan, bisa berisi pasangan yang
        # mustahil. Diperbaiki alih-alih dilaporkan: get_settings dipanggil di
        # dalam loop, dan melempar di situ mematikan penjadwalnya.
        merged["run_hour_end"] = DEFAULT_RUN_HOUR_END
    merged["enabled"] = bool(merged["enabled"])
    return merged


def save_settings(patch: dict) -> dict:
    """Merge patch over the stored settings and persist.

    Validation is deliberately strict: these values drive unattended runs, so a
    typo must fail at save time in the dashboard, not at 08:00 in a log nobody
    reads.
    """
    if not isinstance(patch, dict):
        raise ValueError("settings must be an object")
    current = get_settings()
    allowed = {"enabled", "run_hour", "run_hour_end", "timezone", "delivery"}
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")

    if "enabled" in patch:
        current["enabled"] = bool(patch["enabled"])
    if "run_hour" in patch:
        try:
            hour = int(patch["run_hour"])
        except (TypeError, ValueError):
            raise ValueError("run_hour must be an integer 0-23")
        if not 0 <= hour <= 23:
            raise ValueError("run_hour must be between 0 and 23")
        current["run_hour"] = hour
    if "run_hour_end" in patch:
        nilai = patch["run_hour_end"]
        if nilai is None or str(nilai).strip() == "":
            # Kosong berarti "sampai habis hari" - itulah 23:59 yang diminta.
            current["run_hour_end"] = DEFAULT_RUN_HOUR_END
        else:
            try:
                akhir = int(nilai)
            except (TypeError, ValueError):
                raise ValueError("run_hour_end must be an integer 0-24")
            if not 1 <= akhir <= 24:
                raise ValueError("run_hour_end must be between 1 and 24")
            current["run_hour_end"] = akhir
    if "timezone" in patch:
        tz = str(patch["timezone"] or "").strip() or DEFAULT_TIMEZONE
        try:
            ZoneInfo(tz)
        except Exception:
            raise ValueError(f"unknown timezone: {tz}")
        current["timezone"] = tz
    if "delivery" in patch:
        d = patch["delivery"]
        if not isinstance(d, dict):
            raise ValueError("delivery must be an object")
        if "url" in d:
            current["delivery"]["url"] = str(d["url"] or "").strip()
        if "secret" in d:
            current["delivery"]["secret"] = str(d["secret"] or "")
        if "file_field" in d:
            field = str(d["file_field"] or "").strip() or "file"
            if not field.replace("_", "").replace("-", "").isalnum():
                raise ValueError("file_field must be a simple form field name")
            current["delivery"]["file_field"] = field
        if "headers" in d:
            headers = d["headers"]
            if not isinstance(headers, list):
                raise ValueError("delivery.headers must be a list")
            cleaned = []
            for row in headers:
                if not isinstance(row, dict):
                    raise ValueError("each header must be {name, value}")
                name = str(row.get("name") or "").strip()
                if not name:
                    continue
                if any(ch in name for ch in "\r\n:"):
                    raise ValueError(f"invalid header name: {name!r}")
                value = str(row.get("value") or "")
                if "\r" in value or "\n" in value:
                    raise ValueError(f"invalid header value for {name!r}")
                cleaned.append({"name": name, "value": value})
            current["delivery"]["headers"] = cleaned
    # Diperiksa SETELAH semua field dipasang: satu permintaan bisa mengubah
    # run_hour dan run_hour_end sekaligus, dan PASANGANNYA yang harus sah.
    if current["run_hour_end"] <= current["run_hour"]:
        raise ValueError(
            f"run_hour_end ({current['run_hour_end']}) must be later than "
            f"run_hour ({current['run_hour']})")
    _write(_SETTINGS, current)
    return current


# --- subscriptions -------------------------------------------------------------

def list_subscriptions() -> list:
    subs = _read(_SUBSCRIPTIONS, [])
    return subs if isinstance(subs, list) else []


def find_subscription(sub_id=None, channel_id=None) -> Optional[dict]:
    for sub in list_subscriptions():
        if sub_id and sub.get("id") == sub_id:
            return sub
        if channel_id and sub.get("channel_id") == channel_id:
            return sub
    return None


def add_subscription(channel_id: str, title: str = "", handle: str = "",
                     lease_expires_at: Optional[str] = None) -> dict:
    channel_id = str(channel_id or "").strip()
    if not channel_id:
        raise ValueError("channel_id is required")
    with _lock:
        subs = list_subscriptions()
        for sub in subs:
            if sub.get("channel_id") == channel_id:
                return sub
        sub = {
            "id": str(uuid.uuid4()),
            "channel_id": channel_id,
            "title": title or channel_id,
            "handle": handle or "",
            "status": "active",
            "created_at": _now_iso(),
            "lease_expires_at": lease_expires_at,
        }
        subs.append(sub)
        _write(_SUBSCRIPTIONS, subs)
        return sub


def update_subscription(sub_id: str, **fields) -> Optional[dict]:
    with _lock:
        subs = list_subscriptions()
        updated = None
        for sub in subs:
            if sub.get("id") == sub_id:
                sub.update(fields)
                updated = sub
                break
        if updated is not None:
            _write(_SUBSCRIPTIONS, subs)
        return updated


def remove_subscription(sub_id: str) -> Optional[dict]:
    with _lock:
        subs = list_subscriptions()
        gone = next((s for s in subs if s.get("id") == sub_id), None)
        if gone is None:
            return None
        subs = [s for s in subs if s.get("id") != sub_id]
        _write(_SUBSCRIPTIONS, subs)
        # Drop that channel's still-waiting discoveries too; anything already
        # queued as a job is History's business, not ours.
        items = list_pending()
        kept = [p for p in items
                if p.get("channel_id") != gone.get("channel_id")
                or p.get("status") in ("queued", "running", "done")]
        if len(kept) != len(items):
            _write(_PENDING, kept)
        return gone


# --- pending queue -------------------------------------------------------------

PENDING_STATUSES = ("new", "retry", "queued", "running", "done", "failed", "skip")


def list_pending() -> list:
    items = _read(_PENDING, [])
    if not isinstance(items, list):
        return []
    items.sort(key=lambda p: str(p.get("published") or ""), reverse=True)
    return items


def find_pending(video_id: str) -> Optional[dict]:
    for item in list_pending():
        if item.get("video_id") == video_id:
            return item
    return None


def add_pending(video: dict) -> tuple:
    """Insert one discovered video, deduplicated by video_id.

    Returns (item, created). WebSub push and the RSS fallback both call this, so
    a video announced twice is stored once.
    """
    video_id = str(video.get("video_id") or "").strip()
    if not video_id:
        raise ValueError("video_id is required")
    with _lock:
        items = _read(_PENDING, [])
        for item in items:
            if item.get("video_id") == video_id:
                return item, False
        item = {
            "video_id": video_id,
            "channel_id": video.get("channel_id") or "",
            "channel_title": video.get("channel_title") or "",
            "title": video.get("title") or "",
            "url": video.get("url") or f"https://www.youtube.com/watch?v={video_id}",
            "published": video.get("published") or _now_iso(),
            "discovered_at": _now_iso(),
            "status": "new",
            "attempts": 0,
            "next_attempt_at": 0,
            "job_id": None,
            "last_error": None,
        }
        items.append(item)
        _write(_PENDING, items)
        return item, True


def update_pending(video_id: str, **fields) -> Optional[dict]:
    with _lock:
        items = _read(_PENDING, [])
        updated = None
        for item in items:
            if item.get("video_id") == video_id:
                item.update(fields)
                updated = item
                break
        if updated is not None:
            _write(_PENDING, items)
        return updated


def reset_pending(video_id: str) -> Optional[dict]:
    """Force one failed/skipped video back to 'new' (dashboard action)."""
    return update_pending(video_id, status="new", attempts=0,
                          next_attempt_at=0, last_error=None, job_id=None)


def pending_for_run() -> list:
    return [p for p in list_pending() if p.get("status") == "new"]


def pending_due_retries(now=None) -> list:
    now = time.time() if now is None else now
    due = []
    for item in list_pending():
        if item.get("status") != "retry":
            continue
        try:
            when = float(item.get("next_attempt_at") or 0)
        except (TypeError, ValueError):
            when = 0
        if when <= now:
            due.append(item)
    return due


def pending_in_flight() -> list:
    """Video yang jobnya masih berjalan - sudah diambil, belum tuntas.

    Status "queued" dipasang saat job dibuat dan baru diganti saat jobnya
    selesai, jadi ia mencakup seluruh masa hidup job. Dipakai sebagai rem
    "satu per satu": selama daftar ini tidak kosong, tidak ada job baru
    yang dimulai.
    """
    return [p for p in list_pending() if p.get("status") == "queued"]


def claim_pending(video_id: str, *expected: str, **fields) -> Optional[dict]:
    """Pindahkan status sebuah item HANYA kalau statusnya masih "expected".

    Mengembalikan item kalau transisi terjadi, None kalau tidak. Dipakai
    untuk menempelkan efek samping sekali-jalan (notifikasi Telegram) pada
    perubahan status: dua pemanggil yang berjalan bersamaan menghasilkan
    tepat satu pemenang, jadi pesannya tidak mungkin terkirim dua kali.
    """
    with _lock:
        items = _read(_PENDING, [])
        for item in items:
            if item.get("video_id") != video_id:
                continue
            if expected and item.get("status") not in expected:
                return None
            item.update(fields)
            _write(_PENDING, items)
            return item
    return None


def mark_queued(video_id: str, job_id: str) -> Optional[dict]:
    # CAS, bukan update buta: notifikasi "mulai" menempel pada keberhasilan
    # transisi ini, jadi harus ada tepat satu pemanggil yang berhasil.
    return claim_pending(video_id, "new", "retry", status="queued",
                         job_id=job_id, next_attempt_at=0, last_error=None)


def mark_pending_failure(video_id: str, error: str, retry_after_seconds: int = 1800,
                         permanent: bool = False) -> Optional[dict]:
    item = find_pending(video_id) or {}
    attempts = int(item.get("attempts") or 0) + 1
    if permanent:
        # CAS: "skip" itu final, jadi transisinya hanya boleh terjadi sekali.
        # Notifikasi "dilewati" menempel pada kemenangannya - tanpa ini,
        # pemanggil kedua (pass harian menyusul retry sweep) mengirim pesan
        # yang sama lagi.
        return claim_pending(video_id, "new", "retry", status="skip",
                             attempts=attempts,
                             last_error=(error or "")[:500])
    if attempts >= 3:
        return update_pending(video_id, status="failed", attempts=attempts,
                              last_error=(error or "")[:500], next_attempt_at=0)
    return update_pending(video_id, status="retry", attempts=attempts,
                          last_error=(error or "")[:500],
                          next_attempt_at=time.time() + retry_after_seconds)


def mark_job_finished(job_id: str, ok: bool,
                      error: Optional[str] = None) -> Optional[dict]:
    """Called when an automation job reaches a terminal state.

    Mengembalikan item antreannya, atau None kalau job ini bukan milik
    autopilot. Pemanggil memakai status akhirnya untuk memutuskan apakah
    pesan kegagalan masih boleh berbunyi "akan dicoba lagi".
    """
    for item in list_pending():
        if item.get("job_id") != job_id:
            continue
        if item.get("status") != "queued":
            # Sudah dituntaskan sebelumnya. Mengembalikan None membuat
            # pemanggil melewati notifikasinya, jadi satu job tetap satu pesan.
            return None
        if ok:
            return claim_pending(item["video_id"], "queued", status="done",
                                 last_error=None)
        return mark_pending_failure(item["video_id"], error or "job failed")
    return None


# --- schedule ------------------------------------------------------------------

def get_schedule() -> dict:
    data = _read(_SCHEDULE, {"last_run_date": None})
    return data if isinstance(data, dict) else {"last_run_date": None}


def set_last_run(local_date: str):
    _write(_SCHEDULE, {"last_run_date": local_date})


def local_now(settings=None, now=None) -> datetime:
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    return now.astimezone(_zone(settings.get("timezone")))


def in_window(settings=None, now=None) -> bool:
    """True kalau jam lokal sekarang ada DI DALAM jam operasional.

    Batas akhirnya EKSKLUSIF: window 8-15 berarti pekerjaan baru boleh MULAI
    sampai 14:59. Job yang sudah mulai tidak pernah diperiksa lagi - begitu
    diambil, ia selesai sampai tuntas, termasuk yang lewat batas.
    """
    settings = settings or get_settings()
    local = local_now(settings, now)
    mulai = int(settings.get("run_hour", DEFAULT_RUN_HOUR))
    akhir = int(settings.get("run_hour_end", DEFAULT_RUN_HOUR_END))
    return mulai <= local.hour < akhir


def _as_timestamp(now) -> float:
    """Terima datetime (tes) maupun detik Unix (loop). None = sekarang."""
    if now is None:
        return time.time()
    if isinstance(now, datetime):
        return now.timestamp()
    return float(now)


def next_to_start(settings=None, now=None):
    """Item berikutnya yang boleh dimulai, atau None kalau belum ada yang boleh.

    Urutannya sengaja:
      1. ada job jalan          -> None (rem satu per satu)
      2. retry yang jatuh tempo -> itu (retry TIDAK menunggu jam)
      3. di luar jam            -> None (video tinggal di antrean)
      4. video baru             -> yang paling lama menunggu

    Menggantikan is_due(). Bedanya: ini menjawab "boleh mulai SATU sekarang?"
    dan dipanggil tiap menit, bukan "sudah waktunya pass harian?".
    """
    if pending_in_flight():
        return None
    # pending_due_retries() bekerja dengan detik Unix sementara in_window()
    # bekerja dengan datetime; diterjemahkan di sini supaya pemanggilnya
    # (loop dan tes) boleh memberi salah satu.
    jatuh_tempo = pending_due_retries(_as_timestamp(now))
    if jatuh_tempo:
        return jatuh_tempo[0]
    settings = settings or get_settings()
    if not in_window(settings, now):
        return None
    baru = pending_for_run()
    return baru[0] if baru else None


def should_poll(last_poll_ts, settings=None, now=None, interval_minutes=None) -> bool:
    settings = settings or get_settings()
    if not settings.get("enabled") or not list_subscriptions():
        return False
    interval = interval_minutes
    if interval is None:
        # 1 menit: video baru harus terlihat secepatnya. yt-dlp terukur ~0,7
        # detik per channel jadi bebannya kecil, tapi ini 60x lebih sering dari
        # sebelumnya dan YouTube bisa membalas dengan rate-limit - naikkan
        # lewat env ini kalau WARNING yt-dlp mulai berulang.
        interval = int(os.environ.get("AUTOMATION_POLL_MINUTES", "1"))
    return (time.time() - float(last_poll_ts or 0)) >= interval * 60


# --- delivery outbox -----------------------------------------------------------

def outbox_zip_path(job_id: str) -> str:
    return os.path.join(outbox_dir(), f"{job_id}.zip")


def outbox_state_path(job_id: str) -> str:
    return os.path.join(outbox_dir(), f"{job_id}.json")


def outbox_put(job_id: str, state: dict) -> dict:
    with _lock:
        ensure()
        path = outbox_state_path(job_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return state


def outbox_get(job_id: str) -> Optional[dict]:
    try:
        with open(outbox_state_path(job_id), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"WARNING: automation: unreadable outbox state for {job_id}: {e}")
        return None


def outbox_list() -> list:
    directory = outbox_dir()
    try:
        names = [n for n in os.listdir(directory) if n.endswith(".json")]
    except FileNotFoundError:
        return []
    entries = []
    for name in names:
        state = outbox_get(name[:-len(".json")])
        if state:
            entries.append(state)
    entries.sort(key=lambda s: str(s.get("created_at") or ""), reverse=True)
    return entries


def outbox_remove(job_id: str) -> bool:
    with _lock:
        removed = False
        for path in (outbox_state_path(job_id), outbox_zip_path(job_id)):
            try:
                os.remove(path)
                removed = True
            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"WARNING: automation: could not remove {path}: {e}")
        return removed


def outbox_due(now=None) -> list:
    """Entries that still need a send attempt (3 attempts max)."""
    now = time.time() if now is None else now
    due = []
    for state in outbox_list():
        if state.get("status") == "sent":
            continue
        if int(state.get("attempts") or 0) >= 3:
            continue
        try:
            when = float(state.get("next_attempt_at") or 0)
        except (TypeError, ValueError):
            when = 0
        if when <= now:
            due.append(state)
    return due
