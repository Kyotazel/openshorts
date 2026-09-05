"""Tests for LLM stage progress lines (attempt + heartbeat).

The detail pass sat silent for ~106s in prod: one attempt waited ~45s,
failed, backed off, and the retry waited ~60s more — with only the retry
line in between. These tests pin the replacement: an attempt-start row,
a heartbeat while the blocking API call runs, and a restart row after
the backoff, all through main's helpers so the log panel stays alive.
"""
import threading
import time

import pytest

main = pytest.importorskip("main")


def test_attempt_line_format():
    assert main._llm_attempt_line("detail", 2, 3) == \
        "🤖 Detail pass (attempt 2/3)…"


def test_attempt_line_score_label():
    assert main._llm_attempt_line("score", 1, 3) == \
        "🤖 Score pass (attempt 1/3)…"


def test_heartbeat_line_format():
    assert main._llm_waiting_line("detail", 40.0) == \
        "🤖 Detail pass still waiting for the model… (40s)"


def test_heartbeat_stops_after_call_finishes():
    stop = threading.Event()
    lines = []
    t = main._start_llm_heartbeat("detail", lines.append,
                                  stop=stop, interval=0.05,
                                  now=time.monotonic)
    time.sleep(0.12)
    stop.set()
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert lines, "heartbeat emitted nothing while the call ran"
    assert all("still waiting" in ln for ln in lines)
    n = len(lines)
    time.sleep(0.1)
    assert len(lines) == n, "heartbeat kept printing after stop"
