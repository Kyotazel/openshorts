"""Autopilot delivery: pack a finished job's clips into one ZIP and POST it.

Kept apart from app.py so the ZIP layout and the multipart request can be tested
without booting the server. The caller owns the retry/outbox state machine.

The signature covers the ZIP file's own bytes, not the multipart envelope: httpx
picks a random boundary for every request, so signing the envelope would produce
a different hash on each retry and no receiver could verify it. The receiver
reads the file part and checks X-OpenShorts-Signature against it.
"""
import asyncio
import hashlib
import hmac
import json
import os
import zipfile
from typing import Optional

import httpx

SIGNATURE_HEADER = "X-OpenShorts-Signature"
DEFAULT_TIMEOUT = 600.0
_CAPTIONS_FILE = "captions.json"


def caption_records(clips) -> list:
    """Per-clip social copy for captions.json inside the ZIP.

    These are the fields the dashboard shows under TIKTOK / IG CAPTION, so the
    receiving side gets the same copy without scraping the UI.
    """
    records = []
    for i, clip in enumerate(clips or []):
        clip = clip or {}
        records.append({
            "index": i + 1,
            "title": clip.get("video_title_for_youtube_short") or clip.get("title") or "",
            "tiktok": clip.get("video_description_for_tiktok") or "",
            "instagram": clip.get("video_description_for_instagram") or "",
            "hook": clip.get("viral_hook_text") or "",
        })
    return records


def build_zip(dest_path: str, clip_files, clips,
              metadata_path: Optional[str] = None) -> int:
    """Write one ZIP per job: the clips, captions.json and (optionally) metadata.

    clip_files is [(index, path)] with a 0-based index, already resolved by the
    caller to the current canonical file for each clip. Videos are already
    compressed, so entries are stored rather than deflated. Returns the size.
    """
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_STORED) as zf:
        for index, path in clip_files:
            zf.write(path, arcname=f"clip_{index + 1:02d}_{os.path.basename(path)}")
        zf.writestr(_CAPTIONS_FILE,
                    json.dumps(caption_records(clips), indent=2, ensure_ascii=False))
        if metadata_path and os.path.exists(metadata_path):
            zf.write(metadata_path, arcname="metadata.json")
    return os.path.getsize(dest_path)


def sign_file(path: str, secret: str, chunk_size: int = 1024 * 1024) -> str:
    """HMAC-SHA256 of a file, streamed so a large ZIP never sits in memory."""
    digest = hmac.new(secret.encode(), digestmod=hashlib.sha256)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


async def post_zip(url: str, zip_path: str, *, file_field: str = "file",
                   headers=None, secret: str = "", fields=None,
                   timeout: float = DEFAULT_TIMEOUT) -> tuple:
    """POST the ZIP as multipart/form-data. Returns (ok, detail)."""
    from security_utils import assert_public_url, UnsafeURLError

    if not url:
        return False, "delivery URL is not configured"

    loop = asyncio.get_event_loop()
    # Opt-out for a target on the operator's own machine or LAN (local dev, or
    # an internal API). Off by default: the URL is stored in settings.json, and
    # without the check anyone who can edit it could point delivery at a
    # metadata endpoint and read what comes back.
    allow_private = os.environ.get(
        "AUTOMATION_ALLOW_PRIVATE_TARGET", "").strip().lower() in ("1", "true", "yes")
    if not allow_private:
        try:
            # Re-resolve at send time: the URL was validated when it was saved,
            # and DNS may point somewhere else by now (same as webhooks).
            await loop.run_in_executor(None, assert_public_url, url)
        except UnsafeURLError as e:
            return False, f"unsafe delivery URL: {e}"

    send_headers = {"User-Agent": "OpenShorts-Automation/1.0"}
    for row in headers or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if name:
            send_headers[name] = str(row.get("value") or "")
    if secret:
        try:
            digest = await loop.run_in_executor(None, sign_file, zip_path, secret)
        except OSError as e:
            return False, f"could not read ZIP to sign: {e}"
        send_headers[SIGNATURE_HEADER] = "sha256=" + digest

    data = {str(k): str(v) for k, v in (fields or {}).items() if v is not None}
    try:
        with open(zip_path, "rb") as fh:
            files = {file_field or "file":
                     (os.path.basename(zip_path), fh, "application/zip")}
            async with httpx.AsyncClient(timeout=timeout,
                                         follow_redirects=False) as client:
                resp = await client.post(url, data=data, files=files,
                                         headers=send_headers)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if resp.status_code < 300:
        return True, ""
    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
