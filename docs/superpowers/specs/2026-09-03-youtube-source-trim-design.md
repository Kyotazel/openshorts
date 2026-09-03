# YouTube source trim (start–end) — design

Date: 2026-09-03
Status: approved in conversation, not implemented
Product: OpenShorts URL ingest (dashboard, `POST /api/process`, MCP `process_video`, `main.py` CLI). File upload, `upload_id`, and thumbnail-session handover are out of scope for v1.

## Problem

A YouTube (or other yt-dlp) URL is downloaded, transcribed, analysed, and billed as the **whole** video. Operators who already know the usable window — e.g. `12:45`–`20:21` of a two-hour podcast — still pay Whisper, Gemini, layout picking, and cloud minutes for the rest.

They want that window to **become the source** before anything else runs, so the rest of the pipeline sees a short file and never learns the original duration.

## Locked decisions

| Topic | Decision |
|---|---|
| Window | Start **and** end. Second precision, not minutes. `12:45`–`20:21` is 765s–1221s. |
| Absent | Both omitted → identical to today (whole video). |
| One-sided | Exactly one of start/end set → 400. |
| Mechanism | Download the full file (existing YouTube path), then ffmpeg-cut the window, then **replace** the source with the cut. No `yt-dlp --download-sections` in v1. |
| Billing | Cloud reserves `ceil((end − start) / 60)` minutes, minimum 1. Not the full title duration. |
| Cut failure | The job **fails**. Never continue on the uncut file (would process hours against a slice reservation). |
| Recut / editor | Only the slice exists on disk. Extending outside the window is impossible, by design. |
| Uploads | v1 does not add start/end on file / `upload_id` / thumbnail handover. |
| Field names | `source_start` and `source_end` (float seconds). Not `start`/`end` (those are clip metadata). |

## Data flow

```
URL [+ source_start + source_end]
  → probe metadata (full duration + max height) — validation / quality gate only
  → reject if full duration is < MIN_SOURCE_SECONDS (existing 45s gate)
  → reject if window is inverted, outside [0, duration], or shorter than MIN_SOURCE_SECONDS
  → reserve ceil((end − start) / 60) minutes (cloud)
  → download_youtube_video (unchanged)
  → ffmpeg cut [start, end], same pattern as clip cuts in main.py
  → delete the full download; the cut file is now input_video
  → transcribe / Gemini / reframe / recut run on the slice only
```

No window → skip the cut step. Existing requests stay byte-identical at the CLI (`--source-start` is not added).

The quality probe still sees the full title (formats do not change because of a window). The short-source gate applies twice: once to the probed title duration, once to `(end − start)` when a window is set.

## Clock format (dashboard only)

API, MCP, and `main.py` speak **seconds**. The dashboard is the only place that accepts a clock string.

| Input | Seconds |
|---|---|
| `12:45` | 765 |
| `1:12:45` | 4365 |
| `765` | 765 |
| `0:00` / `0` | 0 |

Reject (inline, do not submit): empty-but-paired-with-the-other-field, `12.45`, `abc`, `99:99` (minute/second ≥ 60), more than three colon parts.

YouTube `?t=` / `&t=` / `youtu.be/...&t=` (including `t=123`, `t=123s`, `t=1h2m3s`) fills **start** when the URL changes. End stays empty until the user types it. The user can overwrite start. The server does **not** parse `t=` from the URL in v1: a link with `t=` and no `source_end` would be one-sided and 400. The dashboard is what turns `t=` into a start field the user then completes.

## API / MCP / CLI

`POST /api/process` (JSON and form), same optional floats as other generation controls:

```json
{
  "url": "https://www.youtube.com/watch?v=…",
  "acknowledged": true,
  "source_start": 765,
  "source_end": 1221
}
```

MCP `process_video` gains the same two properties (`number`, seconds) and forwards them on the in-process REST call. Omitted → body unchanged.

`main.py`:

```
--source-start 765 --source-end 1221
```

`app.py` appends those flags only when both values survived validation. The subprocess does the cut; the API process does not ffmpeg.

Logs (human): `trimming source 12:45–20:21` (clock form of the same seconds). Internally everything stays seconds.

## Cut

New module `source_trim.py` (one job: window math + ffmpeg + replace). `main.py` calls it after a successful download when both CLI flags are present.

Reuse the clip-cut argv already in `main.py`:

```
ffmpeg -y -ss START -to END -i INPUT
  *video_encode_args(QUALITY_FAST)
  *audio_encode_args()
  TEMP
```

Re-encode on purpose: stream-copy would snap to keyframes and miss the requested seconds. A few minutes of slice is cheap next to a Whisper pass on the full title.

On success:

1. Probe the temp file; if missing, empty, or duration ≲ 0, raise.
2. Delete the full download.
3. `os.replace` temp onto the original download path (`{sanitized}.mp4`).
4. Return that path so `--keep-original`, recut, and source lookup keep working without a second filename.

On ffmpeg non-zero, missing output, or replace failure: raise. `main.py` must not assign `input_video` to the uncut file after a failed trim. The job dir may still hold the full download for debugging; the janitor deletes it with the job. It is never the pipeline input.

No extra resume checkpoint. A restarted `main.py` downloads again, as it does today.

## Metering

`reserve_process_minutes` today probes the full URL/file and `ceil`s that. With a window it must reserve from the slice instead:

```
minutes = max(1, ceil((source_end - source_start) / 60))
```

When both `source_start` and `source_end` are set, reservation **must not** use `probe_url_minutes` (that is the full title). Use the slice length above. URL jobs without a window, and all file jobs, keep today's probe. Sending `source_start=0` and `source_end=<full duration>` is legal and bills the same as no window; the UI simply omits the fields instead.

Do not change proxy-byte accounting. v1 still downloads the full media; minutes of processing are what the plan meters.

## Errors

| Case | Where | Result |
|---|---|---|
| Unparseable clock | Dashboard | Field error, no POST |
| One of start/end set | API | 400 |
| `source_end <= source_start` | API | 400 |
| Window outside probed `[0, duration]` | API | 400, message includes the source duration |
| Slice shorter than `MIN_SOURCE_SECONDS` | API | Same reject as a too-short source |
| Full title shorter than `MIN_SOURCE_SECONDS` | API | Existing reject, unchanged |
| ffmpeg / empty cut | `main.py` | Job `failed`; uncut file not processed |
| Delete of full file fails after a good cut | `main.py` | Log warning; continue on the slice |

## Tests

| Area | Assert |
|---|---|
| Clock parse (dashboard helper) | `12:45`→765, `1:12:45`→4365, `765`→765, `0:00`→0; reject `12.45`, `99:99`, `""` |
| YouTube `t=` | `?t=765`, `&t=1h2m3s`, `youtu.be/x?t=45` → start seconds; no `t=` → start stays empty |
| `/api/process` | No fields → cmd has no `--source-start`. Both valid → both flags. One-sided / inverted / past duration / slice < 45s → 400 |
| Metering | 120 min title, window 765–1221 → reserve 8 minutes, not 120 |
| `source_trim.trim_source` | Fixture video; output duration ≈ `end − start`; original path is the slice; original bytes gone |
| Trim failure | ffmpeg error → exception; caller must not use the uncut path |
| MCP | Both fields forwarded; omitted fields leave the REST body as today |

## Out of scope

- Partial download (`--download-sections`)
- Start-only / duration-from-start windows
- Trimming local uploads
- Server-side parse of YouTube `t=`
- Keeping the full download on disk for recut-beyond-window
- Changing proxy GB billing
