"""Cut a downloaded source down to [start, end] and replace the file in place."""
from __future__ import annotations

import os
import subprocess

from ffmpeg_utils import audio_encode_args, video_encode_args, QUALITY_FAST


class TrimError(RuntimeError):
    """ffmpeg cut failed or produced an empty file."""


def validate_window(start, end, duration=None, min_seconds=0):
    """Raise ValueError if the window is unusable. ``duration`` None skips bounds."""
    if start is None or end is None:
        raise ValueError("source_start and source_end are both required")
    start = float(start)
    end = float(end)
    if end <= start:
        raise ValueError("source_end must be after source_start")
    if start < 0:
        raise ValueError("source_start must be >= 0")
    if duration is not None and duration > 0:
        duration = float(duration)
        if start >= duration or end > duration + 0.05:
            raise ValueError(
                f"window {start:.0f}–{end:.0f}s is outside the source "
                f"({duration:.0f}s)")
    span = end - start
    if min_seconds and 0 < span < min_seconds:
        raise ValueError(
            f"window is {span:.0f}s; need at least {min_seconds}s")
    return start, end


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


def reservation_minutes(start, end):
    """Cloud minutes for a slice. Minimum 1."""
    import math
    return max(1, int(math.ceil((float(end) - float(start)) / 60.0)))


def parse_pair(start, end):
    """Normalize optional start/end. Both missing → (None, None). One set → ValueError."""
    def _f(v):
        if v is None or v == "":
            return None
        return float(v)
    try:
        s, e = _f(start), _f(end)
    except (TypeError, ValueError) as err:
        raise ValueError("source_start and source_end must be numbers") from err
    if s is None and e is None:
        return None, None
    if s is None or e is None:
        raise ValueError("source_start and source_end are both required")
    return s, e
    seconds = max(0, int(round(float(seconds))))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def trim_source(path, start, end, run_ffmpeg=None, probe=None):
    """Re-encode ``path`` to [start, end] and replace it. Returns ``path``."""
    start, end = validate_window(start, end)
    probe = probe or probe_duration
    run_ffmpeg = run_ffmpeg or (
        lambda cmd: subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=3600)
    )
    dirname, base = os.path.split(path)
    tmp = os.path.join(dirname, f".trim_{base}")
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-to", str(end),
        "-i", path,
        *video_encode_args(QUALITY_FAST),
        *audio_encode_args(),
        tmp,
    ]
    try:
        run_ffmpeg(cmd)
        if not os.path.isfile(tmp) or os.path.getsize(tmp) < 100:
            raise TrimError("trim produced an empty file")
        dur = probe(tmp)
        if dur <= 0:
            raise TrimError("trim produced no duration")
        os.replace(tmp, path)
        return path
    except TrimError:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    except Exception as e:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        tail = ""
        err = getattr(e, "stderr", None)
        if err:
            tail = " " + (err.decode(errors="replace") if isinstance(err, bytes) else str(err))[-300:]
        raise TrimError(f"{type(e).__name__}: {e}{tail}") from e
