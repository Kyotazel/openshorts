"""AutoAudit mid-roll: pick a window, overlay the bumper, mix SFX."""
from __future__ import annotations

import os
import random
import re
import subprocess
import time

from ffmpeg_utils import video_encode_args, QUALITY, LOUDNORM_FILTER


def pick_ad_window(clip_duration, ad_duration, words, hook_end=3.0):
    """Return (start, end) in clip-local seconds, or None if it will not fit."""
    valid_start = float(hook_end) + 0.3
    valid_end = float(clip_duration) - float(ad_duration) - 0.4
    if valid_end <= valid_start:
        return None
    best = None
    seq = sorted(words or [], key=lambda w: w["start"])
    for i in range(len(seq) - 1):
        gap0, gap1 = seq[i]["end"], seq[i + 1]["start"]
        gap = gap1 - gap0
        if gap < 0.35:
            continue
        cand = min(max(gap0, valid_start), valid_end)
        if valid_start <= cand <= valid_end and (best is None or gap > best[0]):
            best = (gap, cand)
    start = best[1] if best else random.uniform(valid_start, valid_end)
    return (float(start), float(start) + float(ad_duration))


def words_in_clip(transcript, clip_start, clip_end):
    """Word timestamps shifted to clip-local seconds."""
    words = []
    for seg in (transcript or {}).get("segments") or []:
        for w in seg.get("words") or []:
            ws, we = w.get("start"), w.get("end")
            if ws is None or we is None:
                continue
            if we < clip_start or ws > clip_end:
                continue
            words.append({
                "start": float(ws) - float(clip_start),
                "end": float(we) - float(clip_start),
            })
    return words


def build_overlay_cmd(
    main_path,
    ad_path,
    out_path,
    start,
    ad_duration,
    width,
    height,
    duck=0.75,
    has_ad_audio=True,
):
    end = float(start) + float(ad_duration)
    ms = int(round(float(start) * 1000))
    w, h = int(width), int(height)
    enable = f"between(t,{start},{end})"
    v = (
        f"[1:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1[ad];"
        f"[0:v][ad]overlay=0:0:enable='{enable}'[v]"
    )
    if has_ad_audio:
        a = (
            f";[0:a]volume=enable='{enable}':volume={duck}[a0];"
            f"[1:a]atrim=0:{ad_duration},asetpts=PTS-STARTPTS,"
            f"adelay={ms}|{ms}[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[mix]"
        )
    else:
        a = ";[0:a]anull[mix]"
    # loudnorm must live in the same filtergraph: ffmpeg rejects -af on a
    # stream that already came out of -filter_complex (exit 234).
    if os.environ.get("AUDIO_NORMALIZE", "1").strip() != "0":
        a += f";[mix]{LOUDNORM_FILTER}[a]"
    else:
        a += ";[mix]anull[a]"
    return [
        "ffmpeg", "-y", "-i", main_path, "-i", ad_path,
        "-filter_complex", v + a,
        "-map", "[v]", "-map", "[a]",
        *video_encode_args(QUALITY),
        "-c:a", "aac",
        out_path,
    ]


def _probe_size(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0:s=x", path],
            capture_output=True, text=True, timeout=30,
        )
        w, h = (out.stdout or "1080x1920").strip().split("x")
        return int(w), int(h)
    except (ValueError, subprocess.SubprocessError):
        return 1080, 1920


def _has_audio(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return bool((out.stdout or "").strip())
    except subprocess.SubprocessError:
        return False


def insert_ad_clip(
    video_path,
    ad_path,
    start,
    ad_duration,
    probe_size=None,
    run_ffmpeg=None,
):
    """Overlay the bumper. Returns video_path unchanged if anything fails."""
    probe_size = probe_size or _probe_size
    run_ffmpeg = run_ffmpeg or (
        lambda cmd: subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=1800)
    )
    try:
        width, height = probe_size(video_path)
        has_audio = _has_audio(ad_path)
        dirname, base = os.path.split(video_path)
        out_path = os.path.join(dirname, f"adins_{base}")
        cmd = build_overlay_cmd(
            video_path, ad_path, out_path, start, ad_duration,
            width, height, has_ad_audio=has_audio,
        )
        run_ffmpeg(cmd)
        if not os.path.isfile(out_path) or os.path.getsize(out_path) < 100:
            raise RuntimeError("ad overlay produced an empty file")
        os.replace(out_path, video_path)
        return video_path
    except Exception as e:
        tail = ""
        err = getattr(e, "stderr", None)
        if err:
            tail = " " + (err.decode(errors="replace") if isinstance(err, bytes) else str(err))[-500:]
        print(f"⚠️ Ad insert skipped ({type(e).__name__}: {e}){tail}", flush=True)
        try:
            leftover = os.path.join(os.path.dirname(video_path),
                                    f"adins_{os.path.basename(video_path)}")
            if os.path.isfile(leftover):
                os.remove(leftover)
        except OSError:
            pass
        return video_path


ADINS_RE = re.compile(r"^adins_\d+_(.+)$")


def strip_adins_name(output_dir, filename):
    """Walk ``adins_<unix>_`` prefixes while the parent file still exists."""
    while True:
        m = ADINS_RE.match(filename)
        if not m or not os.path.exists(os.path.join(output_dir, m.group(1))):
            return filename
        filename = m.group(1)


def burn_ad_derived(
    video_path,
    ad_path,
    start,
    ad_duration,
    probe_size=None,
    run_ffmpeg=None,
):
    """Write ``adins_<ts>_<basename>`` next to the source. Raises on failure."""
    probe_size = probe_size or _probe_size
    run_ffmpeg = run_ffmpeg or (
        lambda cmd: subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=1800)
    )
    width, height = probe_size(video_path)
    has_audio = _has_audio(ad_path)
    dirname, base = os.path.split(video_path)
    out_path = os.path.join(dirname, f"adins_{int(time.time())}_{base}")
    cmd = build_overlay_cmd(
        video_path, ad_path, out_path, start, ad_duration,
        width, height, has_ad_audio=has_audio,
    )
    try:
        run_ffmpeg(cmd)
        if not os.path.isfile(out_path) or os.path.getsize(out_path) < 100:
            raise RuntimeError("ad overlay produced an empty file")
        return out_path
    except Exception:
        try:
            if os.path.isfile(out_path):
                os.remove(out_path)
        except OSError:
            pass
        raise
