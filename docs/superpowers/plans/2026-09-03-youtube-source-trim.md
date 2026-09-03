# YouTube Source Trim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let URL jobs take `source_start` + `source_end` (seconds) so ffmpeg cuts that window after download and the rest of the pipeline never sees the full title.

**Architecture:** Pure helpers in `source_clock.py` (dashboard-equivalent parse, for tests) and `source_trim.py` (validate + ffmpeg replace). `app.py` validates and appends CLI flags; `main.py` cuts after `download_youtube_video`. Cloud meters `ceil((end-start)/60)` instead of the title duration.

**Tech Stack:** FastAPI, ffmpeg, pytest, React (`MediaInput.jsx`).

## Global Constraints

- Field names: `source_start` and `source_end` (float seconds). Not `start`/`end`.
- One-sided window → 400. Both omitted → identical to today.
- Cut failure fails the job; never process the uncut file.
- v1 URL ingest only (not file / upload_id / thumbnail session).
- Server does not parse YouTube `t=`; dashboard does.
- Re-encode cut (`QUALITY_FAST` + `audio_encode_args`), not stream-copy.

---

### Task 1: Clock parse + window validate

**Files:**
- Create: `source_clock.py`
- Create: `source_trim.py` (validate + trim)
- Create: `tests/test_source_clock.py`
- Create: `tests/test_source_trim.py`

**Interfaces:**
- `source_clock.parse_clock(text) -> float` or raise `ValueError`
- `source_clock.parse_youtube_t(url) -> float | None`
- `source_trim.validate_window(start, end, duration=None, min_seconds=0) -> None` or raise `ValueError`
- `source_trim.trim_source(path, start, end, run_ffmpeg=None, probe_duration=None) -> path`

- [ ] Tests for the clock table and t= URLs in the spec.
- [ ] `trim_source` on a lavfi-generated mp4: duration ≈ end−start; original path is the slice.
- [ ] ffmpeg error → exception.

---

### Task 2: main.py + app.py + metering + MCP

**Files:**
- Modify: `main.py` (`--source-start`, `--source-end`; cut after download)
- Modify: `app.py` (form/JSON fields, 400s, cmd flags, `reserve_process_minutes` slice)
- Modify: `mcp_server.py` (`process_video` properties + forward)
- Create: `tests/test_source_trim_api.py` (httpx, skip if no app)

**Interfaces:**
- CLI: `--source-start 765 --source-end 1221`
- Log: `trimming source 12:45–20:21`
- Metering: when both set, `minutes = max(1, ceil((end-start)/60))` — do not call `probe_url_minutes` for the reservation size.

---

### Task 3: Dashboard

**Files:**
- Create: `dashboard/src/lib/sourceWindow.js` (same parse rules as `source_clock.py`)
- Modify: `dashboard/src/components/MediaInput.jsx` (URL mode: start/end clock fields; fill start from `t=`)
- Modify: `dashboard/src/App.jsx` (send seconds only when both parse)

Copy: two fields under Video URL, `start` / `end`, placeholders `12:45` and `20:21`.
