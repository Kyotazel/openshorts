"""Realtime download progress lines for the System Logs panel.

yt-dlp's own progress is drawn with carriage returns, which never clear
the line-buffered pipe our log reader uses — so the whole download
looked stuck and then arrived as one giant blob. This reporter prints
our own short newline-flushed lines from yt-dlp's progress_hooks dicts,
throttled so a 2s polling UI sees steady updates instead of spam.
"""
from __future__ import annotations

import time

MIN_INTERVAL_SECONDS = 5.0

# One line per whole percent: steady realtime updates without spam.
# Speed/ETA ride along on that one line, so no extra timer rows.
EMIT_EVERY_PERCENT = 1


def format_bytes(n):
    """1024-based human size: B, KB, MB, GB, …"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    if n < 0:
        n = 0
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value, unit = n, units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024.0
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def format_speed(bps):
    """Bytes/sec → '8.0 MB/s', or None when unknown."""
    if bps is None:
        return None
    try:
        bps = float(bps)
    except (TypeError, ValueError):
        return None
    if bps <= 0:
        return None
    return f"{format_bytes(bps)}/s"


def format_eta(seconds):
    """Seconds → '00:29' / '1:02:03', or None when unknown."""
    if seconds is None:
        return None
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _scaled(n):
    """Float value + unit at the 1024 scale where n reads best."""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(n)
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024.0
    return value, unit


def _format_pair(downloaded_f, total_f):
    """'10.0/100.0 MB' when both share a unit, else full forms."""
    dl_v, dl_u = _scaled(downloaded_f)
    tot_v, tot_u = _scaled(total_f)
    if dl_u == tot_u:
        if dl_u == "B":
            return f"{int(dl_v)}/{int(tot_v)} {dl_u}"
        return f"{dl_v:.1f}/{tot_v:.1f} {dl_u}"
    return f"{format_bytes(downloaded_f)}/{format_bytes(total_f)}"


def percent_of(downloaded, total):
    """Whole percent 0-100, or None when the total is unknown."""
    try:
        downloaded_f = float(downloaded or 0)
        total_f = float(total)
    except (TypeError, ValueError):
        return None
    if total_f <= 0 or downloaded_f < 0:
        return None
    return max(0, min(100, int(downloaded_f * 100 / total_f)))


def format_done(total_bytes, elapsed_seconds=None):
    """Closing summary line once the file lands."""
    line = f"✅ Download complete: {format_bytes(total_bytes)}"
    if elapsed_seconds is not None:
        try:
            eta = format_eta(elapsed_seconds)
        except (TypeError, ValueError):
            eta = None
        if eta:
            line += f" in {eta}"
    return line


def format_line(downloaded, total=None, speed=None, eta=None):
    """One user-visible progress line, or None when nothing to say yet."""
    try:
        downloaded_f = float(downloaded or 0)
    except (TypeError, ValueError):
        return None
    if downloaded_f <= 0 and not total:
        return None
    if total:
        try:
            total_f = float(total)
        except (TypeError, ValueError):
            total_f = 0
        if total_f > 0:
            pct = int(downloaded_f * 100 / total_f)
            line = (f"📥 Downloading… "
                    f"{_format_pair(downloaded_f, total_f)} ({pct}%)")
        else:
            line = f"📥 Downloading… {format_bytes(downloaded_f)} downloaded"
    else:
        line = f"📥 Downloading… {format_bytes(downloaded_f)} downloaded"
    tail = ", ".join(
        p for p in (format_speed(speed), f"ETA {format_eta(eta)}"
                    if format_eta(eta) else None) if p)
    return f"{line} — {tail}" if tail else line


class DownloadProgress:
    """One log line per whole percent from yt-dlp progress_hooks dicts.

    Percent-gated, not time-gated: a new whole percent always emits (so
    realtime progress survives a slow pipe), repeats of the same percent
    never do (so an 8 GB file yields ~100 short rows, not thousands).
    A new filename — or a byte counter that jumps backwards (a fresh
    attempt reusing the hook) — resets the gate so the restart shows.
    """

    def __init__(self, emit=print, now=time.monotonic,
                 min_interval=MIN_INTERVAL_SECONDS,
                 emit_every_percent=EMIT_EVERY_PERCENT):
        self._emit = emit
        self._now = now
        self._min_interval = min_interval
        self._emit_every_percent = emit_every_percent
        self._last_emit = None
        self._last_bytes = None
        self._last_percent = None
        self._filename = None

    def _reset(self, filename):
        self._filename = filename
        self._last_emit = None
        self._last_bytes = None
        self._last_percent = None

    def update(self, d):
        if not isinstance(d, dict) or d.get("status") != "downloading":
            return None
        filename = d.get("filename")
        downloaded = d.get("downloaded_bytes") or 0
        if filename != self._filename:
            self._reset(filename)
        try:
            went_backwards = (self._last_bytes is not None
                              and float(downloaded) < float(self._last_bytes))
        except (TypeError, ValueError):
            went_backwards = False
        if went_backwards:
            self._last_emit = None
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        pct = percent_of(downloaded, total)
        line = format_line(
            downloaded,
            total=total,
            speed=d.get("speed"),
            eta=d.get("eta"),
        )
        if line is None:
            return None
        now = self._now()
        if pct is not None and self._last_percent is not None \
                and not went_backwards:
            # Same percent as the last emitted row: never repeat. A new
            # percent always passes, even inside the throttle window.
            if pct // self._emit_every_percent \
                    == self._last_percent // self._emit_every_percent:
                self._last_bytes = downloaded
                return None
        elif self._last_emit is not None and not went_backwards \
                and now - self._last_emit < self._min_interval:
            self._last_bytes = downloaded
            return None
        self._last_emit = now
        self._last_bytes = downloaded
        if pct is not None:
            self._last_percent = pct
        self._emit(line)
        return line

    def finished(self, d):
        """A file completed — the next file must emit immediately."""
        self._reset(d.get("filename") if isinstance(d, dict) else None)
