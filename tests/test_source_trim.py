import os
import subprocess

import pytest

import source_trim as st


def test_parse_pair_none():
    assert st.parse_pair(None, None) == (None, None)


def test_parse_pair_one_sided():
    with pytest.raises(ValueError):
        st.parse_pair(1, None)


def test_reservation_minutes_ceils_slice():
    assert st.reservation_minutes(765, 1221) == 8
    assert st.reservation_minutes(0, 10) == 1


def test_format_clock():
    # main.py's trim log line calls this; it must exist and round-trip
    # the dashboard's mm:ss convention.
    assert st.format_clock(765) == "12:45"
    assert st.format_clock(4365) == "1:12:45"
    assert st.format_clock(0) == "0:00"


def test_validate_inverted():
    with pytest.raises(ValueError):
        st.validate_window(20, 10)


def test_validate_outside_duration():
    with pytest.raises(ValueError, match="outside"):
        st.validate_window(10, 50, duration=40)


def test_validate_slice_too_short():
    with pytest.raises(ValueError, match="at least"):
        st.validate_window(0, 10, min_seconds=45)


def test_trim_replaces_path(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIO_NORMALIZE", "0")
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=4",
         "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
         "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-t", "4",
         str(src)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    before = src.stat().st_size
    out = st.trim_source(str(src), 1.0, 2.5)
    assert out == str(src)
    dur = st.probe_duration(str(src))
    assert 1.2 <= dur <= 1.8
    assert src.stat().st_size < before or dur < 3.5


def test_trim_ffmpeg_failure_does_not_replace(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"not-a-video")
    orig = src.read_bytes()

    def boom(cmd):
        raise RuntimeError("ffmpeg exploded")

    with pytest.raises(st.TrimError):
        st.trim_source(str(src), 0, 1, run_ffmpeg=boom, probe=lambda p: 1.0)
    assert src.read_bytes() == orig
