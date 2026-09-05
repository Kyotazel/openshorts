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


def format_clock(seconds):
    """Seconds → dashboard clock: 765 → '12:45', 4365 → '1:12:45'."""
    seconds = max(0, int(round(float(seconds))))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def trim_source(path, start, end, run_ffmpeg=None, probe=None,
                on_progress=None):
    """Re-encode ``path`` to [start, end] and replace it. Returns ``path``.

    ``on_progress`` receives ffmpeg -progress chunks (or elapsed/total
    pairs from a custom runner) so the caller can print percent rows;
    ``None`` keeps the old silent behavior.
    """
    start, end = validate_window(start, end)
    total = end - start
    probe = probe or probe_duration
    if run_ffmpeg is None:
        import trim_progress as _tp

        def run_ffmpeg(cmd, _tp=_tp, _total=total, _cb=on_progress):
            reporter = _tp.TrimProgress(
                _total,
                emit=(lambda line: print(line, flush=True)) if _cb is None
                else (lambda line: (_cb(line), print(line, flush=True))[1]),
            )
            cmd = list(cmd)
            try:
                prog_idx = cmd.index("-progress")
                cmd[prog_idx + 1] = "pipe:1"
            except (ValueError, IndexError):
                cmd[1:1] = ["-progress", "pipe:1", "-nostats"]
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1)
            chunks, stderr_tail = [], []
            try:
                for raw in iter(proc.stdout.readline, ""):
                    chunks.append(raw)
                    if "progress=" in raw:
                        reporter.feed("".join(chunks))
                        chunks = []
                _, stderr = proc.communicate(timeout=3600)
                stderr_tail = stderr or ""
                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(
                        proc.returncode, cmd, stderr=stderr_tail)
                reporter.feed("progress=end\n")
                return None
            finally:
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass

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
