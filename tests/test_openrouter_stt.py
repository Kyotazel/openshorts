"""OpenRouter Whisper path: 5-minute chunks, word timestamps, env gate."""
import json

import pytest

import openrouter_stt as ostt


VERBOSE = {
    "text": "Hello world from chunk.",
    "language": "en",
    "duration": 4.0,
    "words": [
        {"word": "Hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.4, "end": 0.9},
        {"word": "from", "start": 1.0, "end": 1.3},
        {"word": "chunk.", "start": 1.3, "end": 1.8},
    ],
    "segments": [
        {"start": 0.0, "end": 0.9, "text": "Hello world"},
        {"start": 1.0, "end": 1.8, "text": "from chunk."},
    ],
}


class TestConfiguredModel:
    def test_unset_means_local(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_TRANSCRIBE_MODEL", raising=False)
        assert ostt.configured_model() is None

    def test_blank_means_local(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_TRANSCRIBE_MODEL", "  ")
        assert ostt.configured_model() is None

    def test_set_returns_stripped_id(self, monkeypatch):
        monkeypatch.setenv(
            "OPENROUTER_TRANSCRIBE_MODEL", " openai/whisper-large-v3-turbo ")
        assert ostt.configured_model() == "openai/whisper-large-v3-turbo"

    def test_chunks_are_five_minutes(self):
        assert ostt.CHUNK_SECONDS == 300


class TestTranscriptFromResponse:
    def test_words_are_offset_by_chunk_start_and_get_leading_space(self):
        t = ostt.transcript_from_response(VERBOSE, chunk_start=300.0)
        words = [w for s in t["segments"] for w in s["words"]]
        assert [w["word"] for w in words] == [
            " Hello", " world", " from", " chunk."]
        assert words[0]["start"] == pytest.approx(300.0)
        assert words[1]["start"] == pytest.approx(300.4)
        assert words[-1]["end"] == pytest.approx(301.8)
        json.dumps(t)

    def test_language_and_full_text(self):
        t = ostt.transcript_from_response(VERBOSE, chunk_start=0.0)
        assert t["language"] == "en"
        assert "Hello world" in t["text"]

    def test_no_words_is_unusable(self):
        with pytest.raises(ostt.NoWordTimestamps):
            ostt.transcript_from_response(
                {"text": "hi", "language": "en", "segments": []},
                chunk_start=0.0,
            )


class TestMergeChunks:
    def test_concatenates_absolute_segments(self):
        a = ostt.transcript_from_response(VERBOSE, chunk_start=0.0)
        b = ostt.transcript_from_response(VERBOSE, chunk_start=300.0)
        merged = ostt.merge_chunk_transcripts([a, b])
        assert len(merged["segments"]) == 4
        assert merged["segments"][2]["start"] == pytest.approx(300.0)
        assert merged["text"].count("Hello world") == 2


class TestTranscribeMediaRouting:
    def test_openrouter_runs_when_model_env_is_set(self, monkeypatch):
        sentinel = {"text": "api", "language": "en", "segments": []}
        monkeypatch.setenv(
            "OPENROUTER_TRANSCRIBE_MODEL", "openai/whisper-large-v3-turbo")
        monkeypatch.setattr("transcribe_backends._has_audio_stream", lambda p: True)
        monkeypatch.setattr(ostt, "transcribe", lambda path: sentinel)
        import transcribe_backends as tb
        monkeypatch.setattr(
            tb, "_transcribe_with_whisper",
            lambda p: (_ for _ in ()).throw(AssertionError("local should not run")))
        assert tb.transcribe_media("video.mp4") is sentinel

    def test_falls_back_to_local_when_api_fails(self, monkeypatch):
        local = {"text": "local", "language": "en", "segments": []}
        monkeypatch.setenv(
            "OPENROUTER_TRANSCRIBE_MODEL", "openai/whisper-large-v3-turbo")
        monkeypatch.setattr("transcribe_backends._has_audio_stream", lambda p: True)
        monkeypatch.setattr(
            ostt, "transcribe",
            lambda path: (_ for _ in ()).throw(RuntimeError("timeout")))
        import transcribe_backends as tb
        monkeypatch.setattr(tb, "_transcribe_with_whisper", lambda p: local)
        assert tb.transcribe_media("video.mp4") is local

    def test_unset_env_stays_on_local_whisper(self, monkeypatch):
        local = {"text": "local", "language": "en", "segments": []}
        monkeypatch.delenv("OPENROUTER_TRANSCRIBE_MODEL", raising=False)
        monkeypatch.setattr("transcribe_backends._has_audio_stream", lambda p: True)
        monkeypatch.setattr(
            ostt, "transcribe",
            lambda p: (_ for _ in ()).throw(AssertionError("api should not run")))
        import transcribe_backends as tb
        monkeypatch.setattr(tb, "_transcribe_with_whisper", lambda p: local)
        assert tb.transcribe_media("video.mp4") is local
