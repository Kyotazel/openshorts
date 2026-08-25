"""Captions on the seam of SPLIT scenes: the render records which stretches
are stacked (layout_ranges sidecar), and generate_ass anchors the word events
inside them mid-frame (\\an5) while everything else keeps the style's own
alignment. A clip mixes both, so the placement is per event, not per style."""
import json
import os

import pytest

import layout_ranges
import subtitles


def _transcript(words):
    return {"language": "en", "segments": [{
        "start": words[0][1], "end": words[-1][2],
        "words": [{"word": w, "start": s, "end": e} for w, s, e in words]}]}


class TestSidecar:
    def test_write_then_read_roundtrip(self, tmp_path):
        clip = str(tmp_path / "clip_1.mp4")
        layout_ranges.write(clip, [(0, 4.5, "TRACK"), (4.5, 10, "SPLIT")])
        assert os.path.exists(clip + ".layout.json")
        got = layout_ranges.read(clip)
        assert got == [{"start": 0.0, "end": 4.5, "layout": "track"},
                       {"start": 4.5, "end": 10.0, "layout": "split"}]
        assert layout_ranges.split_ranges(got) == [(4.5, 10.0)]

    def test_missing_sidecar_is_empty(self, tmp_path):
        assert layout_ranges.read(str(tmp_path / "nope.mp4")) == []

    def test_garbage_is_ignored_not_raised(self, tmp_path):
        clip = str(tmp_path / "c.mp4")
        with open(clip + ".layout.json", "w") as f:
            f.write("{not json")
        assert layout_ranges.read(clip) == []
        assert layout_ranges.normalise([{"start": "x"}, (3, 1, "split"), (1, 2, "SPLIT"), 7]) == [
            {"start": 1.0, "end": 2.0, "layout": "split"}]

    def test_write_failure_does_not_raise(self, tmp_path):
        layout_ranges.write(str(tmp_path / "missing_dir" / "c.mp4"), [(0, 1, "split")])


class TestSeamCaptions:
    def _events(self, tmp_path, split_ranges, **kw):
        out = tmp_path / "subs.ass"
        # Leading spaces as Whisper emits them: without them the merge step
        # glues the words into one token and one block.
        words = [(" one", 10.0, 10.4), (" two", 10.5, 10.9),   # single shot
                 (" three", 13.0, 13.4), (" four", 13.5, 13.9)]  # stacked
        ok = subtitles.generate_ass(_transcript(words), 10.0, 14.0, str(out),
                                    max_chars=8, split_ranges=split_ranges, **kw)
        assert ok
        return [l for l in out.read_text(encoding="utf-8-sig").splitlines()
                if l.startswith("Dialogue:")]

    def test_only_split_stretches_move_to_the_seam(self, tmp_path):
        # Ranges are in CLIP seconds, like the events themselves.
        ev = self._events(tmp_path, [(2.5, 4.0)])
        first_block = [e for e in ev if "one" in e]
        second_block = [e for e in ev if "three" in e]
        assert first_block and second_block
        assert all("{\\an5}" not in e for e in first_block)
        assert all(e.split(",,")[-1].startswith("{\\an5}") for e in second_block)

    def test_no_ranges_changes_nothing(self, tmp_path):
        assert all("\\an5" not in e for e in self._events(tmp_path, None))
        assert all("\\an5" not in e for e in self._events(tmp_path, []))

    def test_style_alignment_still_rules_elsewhere(self, tmp_path):
        out = tmp_path / "s.ass"
        subtitles.generate_ass(_transcript([(" hi", 0.0, 0.5)]), 0.0, 1.0, str(out),
                               alignment="top", split_ranges=[(5, 6)])
        header = out.read_text(encoding="utf-8-sig")
        assert ",8,10,10," in header  # Alignment 8 = top in the Style line


class TestCaptionerReadsSidecar:
    def test_auto_caption_uses_sidecar_when_not_told(self, tmp_path, monkeypatch):
        main = pytest.importorskip("main")
        clip = str(tmp_path / "x.mp4")
        open(clip, "wb").close()
        layout_ranges.write(clip, [(0, 3, "SPLIT")])
        seen = {}

        def fake_generate_ass(*a, **kw):
            seen["split_ranges"] = kw.get("split_ranges")
            return False  # stop before ffmpeg
        monkeypatch.setattr(subtitles, "generate_ass", fake_generate_ass)
        main.auto_caption_clip(clip, _transcript([(" a", 0.0, 0.5)]), 0.0, 3.0)
        assert seen["split_ranges"] == [(0.0, 3.0)]
