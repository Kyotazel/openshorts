"""Realtime download progress for the System Logs panel.

yt-dlp draws its own progress with carriage returns, which never survive
the line-buffered pipe reader — the whole download looked stuck, then
arrived as one giant blob. These tests pin the replacement: our own
short newline-flushed lines from the in-process progress_hooks dicts.
"""
import download_progress as dp


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _make(clock=None):
    clock = clock or Clock()
    lines = []
    rep = dp.DownloadProgress(emit=lines.append, now=clock, min_interval=5.0)
    return rep, lines, clock


def _dl(downloaded, total=None, speed=None, eta=None,
        filename="f.webm", status="downloading"):
    d = {"status": status, "downloaded_bytes": downloaded,
         "filename": filename}
    if total is not None:
        d["total_bytes"] = total
    if speed is not None:
        d["speed"] = speed
    if eta is not None:
        d["eta"] = eta
    return d


def test_format_bytes():
    assert dp.format_bytes(0) == "0 B"
    assert dp.format_bytes(512) == "512 B"
    assert dp.format_bytes(1536) == "1.5 KB"
    assert dp.format_bytes(43_190_845) == "41.2 MB"
    assert dp.format_bytes(2_500_000_000) == "2.3 GB"


def test_format_eta():
    assert dp.format_eta(29) == "00:29"
    assert dp.format_eta(3723) == "1:02:03"
    assert dp.format_eta(None) is None


def test_first_update_emits_immediately():
    rep, lines, _ = _make()
    rep.update(_dl(10_485_760, total=104_857_600, speed=8_388_608, eta=29))
    assert lines == [
        "📥 Downloading… 10.0/100.0 MB (10%) — 8.0 MB/s, ETA 00:29",
    ]


def test_same_percent_repeats_are_throttled():
    rep, lines, clock = _make()
    rep.update(_dl(10_485_760, total=104_857_600))  # 10%
    clock.now += 1.0
    rep.update(_dl(11_010_048, total=104_857_600))  # still 10%
    clock.now += 1.0
    rep.update(_dl(11_200_000, total=104_857_600))  # still 10%
    assert len(lines) == 1


def test_update_after_interval_without_new_percent_stays_quiet():
    rep, lines, clock = _make()
    rep.update(_dl(10_485_760, total=104_857_600))
    clock.now += 30.0
    rep.update(_dl(11_010_048, total=104_857_600))  # still 10%
    assert len(lines) == 1


def test_unknown_total_shows_downloaded_only():
    rep, lines, _ = _make()
    rep.update(_dl(12_582_912, speed=8_388_608))
    assert lines == ["📥 Downloading… 12.0 MB downloaded — 8.0 MB/s"]


def test_missing_speed_and_eta_omitted():
    rep, lines, _ = _make()
    rep.update(_dl(10_485_760, total=104_857_600))
    assert lines == ["📥 Downloading… 10.0/100.0 MB (10%)"]


def test_finished_resets_next_file_to_emit_immediately():
    rep, lines, clock = _make()
    rep.update(_dl(10_485_760, total=104_857_600, filename="a.webm"))
    clock.now += 1.0
    rep.finished(_dl(104_857_600, total=104_857_600,
                     filename="a.webm", status="finished"))
    rep.update(_dl(1_048_576, total=52_428_800, filename="b.webm"))
    assert len(lines) == 2


def test_restart_of_same_file_forces_emit():
    rep, lines, clock = _make()
    rep.update(_dl(50_000_000, total=104_857_600))
    clock.now += 1.0
    rep.update(_dl(1_000_000, total=104_857_600))
    assert len(lines) == 2


def test_same_percent_does_not_emit_again():
    rep, lines, clock = _make()
    rep.update(_dl(524_288_000, total=8_589_934_592))  # ~6.1%
    n = len(lines)
    clock.now += 6.0
    rep.update(_dl(530_000_000, total=8_589_934_592))  # still 6%
    assert len(lines) == n


def test_next_percent_emits_despite_throttle():
    rep, lines, clock = _make()
    rep.update(_dl(524_288_000, total=8_589_934_592))  # ~6%
    clock.now += 1.0  # inside the 5s throttle window
    rep.update(_dl(610_000_000, total=8_589_934_592))  # ~7%
    assert len(lines) == 2
    assert "(7%)" in lines[1]


def test_done_line_format():
    assert dp.format_done(8_589_934_592, 3723.5) == \
        "✅ Download complete: 8.0 GB in 1:02:03"


def test_done_line_without_elapsed():
    assert dp.format_done(41_190_845, None) == \
        "✅ Download complete: 39.3 MB"


def test_app_drops_ytdlp_noise_but_keeps_own_rows():
    import re
    src = open("app.py").read()
    m = re.search(r'startswith\(\(\s*((?:"[^"]+",?\s*)+)\)', src)
    assert m, "noise-drop startswith tuple not found in app.py"
    prefixes = re.findall(r'"([^"]+)"', m.group(1))
    for p in ("[download]", "[Merger]", "[debug]", "[info]"):
        assert p in prefixes
    noise = [
        "[download] 4.8% of 8.01GiB at 3.01MiB/s ETA 43:23",
        "[Merger] Merging formats into \"output/x.mp4\"",
        "[debug] Invoking http downloader on \"https://rr7--googlevideo.com/x\"",
        "[info] 0YuvkyfYeoc: Downloading 1 format(s): 315+251",
    ]
    own = [
        "📥 Downloading… 417.2 MB/8.0 GB (5%) — 12.6 MB/s, ETA 10:17",
        "✅ Download complete: 8.0 GB in 10:17",
    ]
    assert all(l.startswith(tuple(prefixes)) for l in noise)
    assert not any(l.startswith(tuple(prefixes)) for l in own)
