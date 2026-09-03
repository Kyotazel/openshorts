import ad_insert as ad


def test_skip_when_clip_too_short():
    assert ad.pick_ad_window(8.0, 5.0, [], hook_end=3.0) is None


def test_prefers_longest_gap_in_valid_window():
    words = [
        {"start": 3.5, "end": 4.0},
        {"start": 4.1, "end": 5.0},
        {"start": 8.0, "end": 9.0},
        {"start": 12.0, "end": 13.0},
    ]
    start, end = ad.pick_ad_window(20.0, 3.0, words, hook_end=3.0)
    assert 4.0 < start < 9.0
    assert end - start == 3.0


def test_random_fallback_stays_in_window(monkeypatch):
    monkeypatch.setattr(ad.random, "uniform", lambda a, b: (a + b) / 2)
    start, end = ad.pick_ad_window(30.0, 3.0, [], hook_end=3.0)
    assert start >= 3.3
    assert end <= 29.6


def test_overlay_enable_uses_window():
    cmd = ad.build_overlay_cmd(
        "a.mp4", "b.mp4", "c.mp4",
        start=12.4, ad_duration=3.0, width=1080, height=1920,
    )
    joined = " ".join(cmd)
    assert "between(t,12.4" in joined
    assert "volume=0.75" in joined or "volume='0.75'" in joined or "volume=enable" in joined
    assert "amix" in joined
    assert "scale=1080:1920" in joined


def test_no_ad_audio_skips_amix():
    cmd = ad.build_overlay_cmd(
        "a.mp4", "b.mp4", "c.mp4",
        start=10, ad_duration=2, width=1080, height=1920, has_ad_audio=False,
    )
    assert "amix" not in " ".join(cmd)


def test_insert_ad_clip_returns_source_on_ffmpeg_failure(tmp_path):
    main = tmp_path / "clip.mp4"
    adf = tmp_path / "ad.mp4"
    main.write_bytes(b"x")
    adf.write_bytes(b"x")

    def boom(cmd):
        raise RuntimeError("ffmpeg exploded")

    out = ad.insert_ad_clip(
        str(main), str(adf), 2.0, 3.0,
        probe_size=lambda p: (1080, 1920),
        run_ffmpeg=boom,
    )
    assert out == str(main)
