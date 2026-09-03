"""OpenRouter speech-to-text: 5-minute chunks, word timestamps.

Gated by OPENROUTER_TRANSCRIBE_MODEL (e.g. openai/whisper-large-v3-turbo).
Auth is LLM_API_KEY. The chat LLM_BASE_URL is never used — that may point
at Ollama — transcription always hits OpenRouter's /audio/transcriptions.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

import httpx

from subtitles import merge_continuation_words

OPENROUTER_STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
CHUNK_SECONDS = 300
_HTTP_TIMEOUT = 120.0

_LANG_ALIASES = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de",
    "indonesian": "id", "portuguese": "pt", "italian": "it",
    "japanese": "ja", "chinese": "zh", "korean": "ko", "arabic": "ar",
    "russian": "ru", "dutch": "nl", "turkish": "tr", "vietnamese": "vi",
    "hindi": "hi", "polish": "pl", "swedish": "sv", "thai": "th",
}


class NoWordTimestamps(ValueError):
    """Provider returned text but no usable word-level timestamps."""


def configured_model():
    raw = (os.environ.get("OPENROUTER_TRANSCRIBE_MODEL") or "").strip()
    return raw or None


def _language_code(resp, text=""):
    raw = str(resp.get("language") or "").strip().lower()
    if len(raw) == 2:
        return raw
    if raw in _LANG_ALIASES:
        return _LANG_ALIASES[raw]
    return "en"


def _stt_get(obj, name, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_dict(obj):
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            return {}
    return {}


def transcript_from_response(resp, chunk_start=0.0):
    """Map one verbose_json body onto the transcribe_media contract.

    Word times are relative to the chunk; ``chunk_start`` shifts them onto
    the source timeline. OpenAI words have no leading space; the pipeline
    requires one on every true word start.
    """
    body = _as_dict(resp) if not isinstance(resp, dict) else resp
    raw_words = list(body.get("words") or [])
    if not raw_words:
        for seg in (body.get("segments") or []):
            raw_words.extend(_stt_get(seg, "words") or [])

    words = []
    for w in raw_words:
        wd = _as_dict(w) if not isinstance(w, dict) else w
        token = str(wd.get("word") or "").strip()
        if not token:
            continue
        start = chunk_start + float(wd.get("start") or 0.0)
        end = chunk_start + float(wd.get("end") or start)
        if end < start:
            end = start
        words.append({
            "word": " " + token,
            "start": float(start),
            "end": float(end),
        })
    if not words:
        raise NoWordTimestamps("OpenRouter response had no word timestamps")

    raw_segments = list(body.get("segments") or [])
    out_segments = []
    if raw_segments:
        for i, seg in enumerate(raw_segments):
            sd = _as_dict(seg) if not isinstance(seg, dict) else seg
            ss = chunk_start + float(sd.get("start") or 0.0)
            se = chunk_start + float(sd.get("end") or ss)
            if i + 1 < len(raw_segments):
                nxt = raw_segments[i + 1]
                nd = _as_dict(nxt) if not isinstance(nxt, dict) else nxt
                next_ss = chunk_start + float(nd.get("start") or se)
                lo = -1e9 if i == 0 else ss
                seg_words = [w for w in words if lo <= w["start"] < next_ss]
            else:
                seg_words = [w for w in words if w["start"] >= ss]
            text = str(sd.get("text") or "").strip()
            if not text:
                text = " ".join(w["word"].strip() for w in seg_words)
            if not seg_words:
                continue
            out_segments.append({
                "start": float(ss),
                "end": float(se),
                "text": text,
                "words": merge_continuation_words(seg_words),
            })
    if not out_segments:
        out_segments.append({
            "start": words[0]["start"],
            "end": words[-1]["end"],
            "text": str(body.get("text") or "").strip()
                    or " ".join(w["word"].strip() for w in words),
            "words": merge_continuation_words(words),
        })

    text = str(body.get("text") or "").strip()
    if not text:
        text = " ".join(s["text"] for s in out_segments if s.get("text"))
    return {
        "text": text,
        "language": _language_code(body, text),
        "segments": out_segments,
    }


def merge_chunk_transcripts(parts):
    if not parts:
        raise NoWordTimestamps("no transcribed chunks")
    segments = []
    texts = []
    language = "en"
    for part in parts:
        segments.extend(part.get("segments") or [])
        if part.get("text"):
            texts.append(part["text"])
        if part.get("language"):
            language = part["language"]
    return {
        "text": " ".join(texts),
        "language": language,
        "segments": segments,
    }


def _ffprobe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60,
        )
        return float((out.stdout or "").strip())
    except (ValueError, TypeError, subprocess.SubprocessError):
        return 0.0


def _split_audio_chunks(media_path, tmpdir):
    pattern = os.path.join(tmpdir, "chunk_%03d.mp3")
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", media_path,
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libmp3lame", "-b:a", "64k",
            "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
            "-reset_timestamps", "1", pattern,
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        timeout=1800,
    )
    return sorted(
        os.path.join(tmpdir, name)
        for name in os.listdir(tmpdir)
        if name.startswith("chunk_") and name.endswith(".mp3")
    )


def _headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/Kyotazel/openshorts",
        "X-Title": "OpenShorts",
    }


def _post_chunk(path, model, api_key, extra):
    with open(path, "rb") as fh:
        files = {"file": (os.path.basename(path), fh, "audio/mpeg")}
        data = [("model", model), *extra]
        resp = httpx.post(
            OPENROUTER_STT_URL,
            headers=_headers(api_key),
            files=files,
            data=data,
            timeout=_HTTP_TIMEOUT,
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"OpenRouter STT {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except json.JSONDecodeError as e:
        raise RuntimeError(f"OpenRouter STT returned non-JSON: {e}") from e


def transcribe_chunk(path, model, api_key):
    """POST one audio file; return the verbose_json body with word timestamps."""
    attempts = [
        [("response_format", "verbose_json"),
         ("timestamp_granularities[]", "word")],
        [("response_format", "verbose_json")],
    ]
    last_err = None
    for extra in attempts:
        try:
            body = _post_chunk(path, model, api_key, extra)
            transcript_from_response(body, chunk_start=0.0)  # validate words
            return body
        except NoWordTimestamps as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("OpenRouter STT failed")


def transcribe(media_path):
    """Split *media_path* into 5-minute chunks and transcribe each via OpenRouter."""
    model = configured_model()
    if not model:
        raise RuntimeError("OPENROUTER_TRANSCRIBE_MODEL is not set")
    api_key = (os.environ.get("LLM_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY is empty — OpenRouter transcription needs it")

    tmpdir = tempfile.mkdtemp(prefix="or_whisper_")
    try:
        print(
            f"🎙️ [ASR] openrouter {model} (chunk {CHUNK_SECONDS}s)",
            flush=True,
        )
        chunks = _split_audio_chunks(media_path, tmpdir)
        if not chunks:
            raise RuntimeError("ffmpeg produced no audio chunks for OpenRouter STT")

        parts = []
        offset = 0.0
        n = len(chunks)
        for i, path in enumerate(chunks):
            pct = int((i / max(n, 1)) * 100)
            print(f"🎙️ Transcribing… {pct}% (chunk {i + 1}/{n})", flush=True)
            duration = _ffprobe_duration(path) or float(CHUNK_SECONDS)
            body = transcribe_chunk(path, model, api_key)
            parts.append(transcript_from_response(body, chunk_start=offset))
            offset += duration
        print("🎙️ Transcribing… 100%", flush=True)
        return merge_chunk_transcripts(parts)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
