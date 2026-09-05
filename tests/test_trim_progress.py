"""Tests for trim progress rows and LLM stage progress lines."""
import threading
import time

import trim_progress as tp


def _make(total=300.0):
    lines = []
    rep = tp.TrimProgress(total, emit=lines.append)
    return rep, lines


def _chunk(out_time_ms=None, end=False):
    parts = []
    if out_time_ms is not None:
        parts.append(f"out_time_ms={out_time_ms}")
    parts.append("progress=end" if end else "progress=continue")
    return "\n".join(parts) + "\n"


def test_parse_progress_time_us():
    assert tp.parse_progress_time_us(_chunk(4_500_000)) == 4_500_000
    assert tp.parse_progress_time_us("progress=end\n") is None
    assert tp.parse_progress_time_us("") is None
    assert tp.parse_progress_time_us("out_time_ms=abc\n") is None


def test_is_progress_end():
    assert tp.is_progress_end(_chunk(end=True))
    assert not tp.is_progress_end(_chunk(1000))


def test_first_percent_emits():
    rep, lines = _make()
    rep.feed(_chunk(3_000_000))
    assert lines == ["✂️ Trimming… 3/300s (1%)"]


def test_same_percent_does_not_repeat():
    rep, lines = _make()
    rep.feed(_chunk(3_000_000))
    rep.feed(_chunk(4_000_000))  # still 1%
    rep.feed(_chunk(5_000_000))  # still 1%
    assert lines == ["✂️ Trimming… 3/300s (1%)"]


def test_next_percent_emits():
    rep, lines = _make()
    rep.feed(_chunk(3_000_000))
    rep.feed(_chunk(6_000_000))
    assert lines[-1] == "✂️ Trimming… 6/300s (2%)"


def test_end_forces_closing_row():
    rep, lines = _make()
    rep.feed(_chunk(3_000_000))
    rep.feed(_chunk(end=True))
    assert lines[-1] == "✂️ Trimming… 300/300s (100%)"


def test_end_without_prior_rows_still_closes():
    rep, lines = _make()
    rep.feed(_chunk(end=True))
    assert lines == ["✂️ Trimming… 300/300s (100%)"]
