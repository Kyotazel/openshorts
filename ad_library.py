"""Disk library for the AutoAudit mid-roll bumper (one active clip)."""
import json
import os
import shutil
import subprocess
import uuid

ALLOWED_EXT = {".mp4", ".mov", ".webm"}
MAX_ITEMS = 20
MANIFEST = "manifest.json"


class LibraryFull(Exception):
    """Too many clips in the instance library."""


class BadAdFile(ValueError):
    """Upload is not a supported video."""


def library_dir():
    override = (os.environ.get("AD_LIBRARY_DIR") or "").strip()
    if override:
        return override
    root = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, "ad_library")


def _manifest_path():
    return os.path.join(library_dir(), MANIFEST)


def _empty():
    return {"active_id": None, "items": []}


def read_manifest():
    path = _manifest_path()
    if not os.path.isfile(path):
        return _empty()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty()
    data.setdefault("active_id", None)
    data.setdefault("items", [])
    return data


def _write_manifest(data):
    os.makedirs(library_dir(), exist_ok=True)
    path = _manifest_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def probe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float((out.stdout or "").strip())
    except (ValueError, TypeError, subprocess.SubprocessError):
        return 0.0


def _ext(name):
    return os.path.splitext(name or "")[1].lower()


def add_item(src_path, original_name):
    ext = _ext(original_name) or _ext(src_path)
    if ext not in ALLOWED_EXT:
        raise BadAdFile("Use mp4, mov, or webm")
    man = read_manifest()
    if len(man["items"]) >= MAX_ITEMS:
        raise LibraryFull(f"Library full ({MAX_ITEMS})")
    item_id = uuid.uuid4().hex[:12]
    filename = f"{item_id}{ext}"
    dest = os.path.join(library_dir(), filename)
    os.makedirs(library_dir(), exist_ok=True)
    shutil.copy2(src_path, dest)
    duration = float(probe_duration(dest) or 0.0)
    item = {
        "id": item_id,
        "filename": filename,
        "original_name": os.path.basename(original_name),
        "duration": duration,
    }
    man["items"].append(item)
    if not man.get("active_id"):
        man["active_id"] = item_id
    _write_manifest(man)
    return item


def activate(item_id):
    man = read_manifest()
    if not any(it["id"] == item_id for it in man["items"]):
        raise KeyError(item_id)
    man["active_id"] = item_id
    _write_manifest(man)
    return man


def delete_item(item_id):
    man = read_manifest()
    item = next((it for it in man["items"] if it["id"] == item_id), None)
    if item is None:
        raise KeyError(item_id)
    path = os.path.join(library_dir(), item["filename"])
    try:
        os.remove(path)
    except OSError:
        pass
    man["items"] = [it for it in man["items"] if it["id"] != item_id]
    if man.get("active_id") == item_id:
        man["active_id"] = None
    _write_manifest(man)
    return man


def active_path():
    man = read_manifest()
    aid = man.get("active_id")
    if not aid:
        return None
    item = next((it for it in man["items"] if it["id"] == aid), None)
    if not item:
        return None
    path = os.path.join(library_dir(), item["filename"])
    return path if os.path.isfile(path) else None


def insert_ad_env(requested, has_active):
    """Job env for INSERT_AD. ``requested`` is True/False/None."""
    if requested is False:
        return {"INSERT_AD": "0"}
    if requested is True or (requested is None and has_active):
        return {"INSERT_AD": "1"}
    return {"INSERT_AD": "0"}
