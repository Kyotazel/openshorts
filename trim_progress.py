"""Progress reporter for the source trim (ffmpeg re-encode of [start, end]).

The trim ran silent for minutes: stdout was DEVNULL, so the panel showed
nothing between "trimming source" and the next stage. The reporter parses
ffmpeg's -progress pipe (out_time_ms) into one line per whole percent —
the same shape as the download progress rows.
"""
from __future__ import annotations

import time

EMIT_EVERY_PERCENT = 1


def parse_progress_time_us(text):
    """Microseconds from an ffmpeg -progress chunk, or None.

    Chunks look like "out_time_ms=4500000\\n...". A final "progress=end"
    chunk may carry no time; the caller treats that as completion.
    """
    if not text:
        return None
    for raw in str(text).replace("\r", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("out_time_ms="):
            try:
                value = int(line.split("=", 1)[1].strip())
            except (TypeError, ValueError):
                return None
            return max(0, value)
    return None


def is_progress_end(text):
    if not text:
        return False
    return any(
        ln.strip() == "progress=end"
        for ln in str(text).replace("\r", "\n").split("\n"))


def percent_of(elapsed_us, total_seconds):
    try:
        total_us = float(total_seconds) * 1_000_000
        elapsed = float(elapsed_us)
    except (TypeError, ValueError):
        return None
    if total_us <= 0 or elapsed < 0:
        return None
    return max(0, min(100, int(elapsed * 100 / total_us)))


def format_line(elapsed_us, total_seconds):
    pct = percent_of(elapsed_us, total_seconds)
    if pct is None:
        return None
    try:
        elapsed_s = float(elapsed_us) / 1_000_000
        total_s = float(total_seconds)
    except (TypeError, ValueError):
        return None
    return f"✂️ Trimming… {elapsed_s:.0f}/{total_s:.0f}s ({pct}%)"


class TrimProgress:
    """Percent-gated rows from ffmpeg -progress chunks.

    A new whole percent always emits; repeats of the same percent never
    do. ``progress=end`` forces the closing 100% row.
    """

    def __init__(self, total_seconds, emit=print,
                 emit_every_percent=EMIT_EVERY_PERCENT):
        self._total = total_seconds
        self._emit = emit
        self._step = emit_every_percent
        self._last_percent = None

    def feed(self, chunk):
        if is_progress_end(chunk):
            line = format_line(float(self._total) * 1_000_000, self._total)
            if line and self._last_percent != 100:
                self._last_percent = 100
                self._emit(line)
            return line
        elapsed_us = parse_progress_time_us(chunk)
        if elapsed_us is None:
            return None
        pct = percent_of(elapsed_us, self._total)
        if pct is None:
            return None
        if self._last_percent is not None and \
                pct // self._step == self._last_percent // self._step:
            return None
        self._last_percent = pct
        line = format_line(elapsed_us, self._total)
        if line is None:
            return None
        self._emit(line)
        return line
