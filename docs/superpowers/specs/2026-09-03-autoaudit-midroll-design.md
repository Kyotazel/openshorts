# AutoAudit mid-roll insert — design

Date: 2026-09-03
Status: approved in conversation, not implemented
Product: OpenShorts self-host (PM2). Cloud multi-user library is out of scope for v1.

## Problem

OpenShorts shorts are talking-head (or layout) cuts with hook + karaoke. AutoAudit needs a **2–5 second branded insert** in the body of each short: the product clip the operator already has, not Pexels and not AI-generated B-roll.

This is a **mid-roll bumper**, not editorial B-roll. It must not cover the hook. It must not pause the podcast: original speech continues, the ad only adds SFX (whoosh / scratch). Karaoke is hidden for those seconds so the product is readable.

## Locked decisions

| Topic | Decision |
|---|---|
| Source | Operator-uploaded MP4. Not Pexels, not generated. |
| Duration | File is already 2–5 s. Use it whole. Do not loop. If a file is ever >5 s, take the first 5 s as a safety cap. If <2 s, use as-is. |
| Library | Disk library, **one active** clip. |
| Per job | Default = the active library clip. Job can disable or (later) pick another. v1 disable-only override is enough; picking another file per job can wait. |
| Count | **One** insert per short. |
| Placement | Not in the hook. Prefer a transcript pause in the valid window; else **random** inside that window (includes “middle”). |
| Picture | Cutaway: ad covers the full 9:16 frame, then talking head returns. Timeline length **does not grow**. |
| Audio | Podcast continues. Duck podcast to **0.75** during the window. Mix in the ad’s audio (SFX). If the ad has no audio track, skip the mix (visual only). |
| Captions | Hidden during the window (ad overlay sits on top of already-burned karaoke). Karaoke resumes after because the overlay ends. |
| Hook overlay | Ad never starts before hook-end + 0.3 s, so hook text and ad do not overlap. |

## Placement

Clip-local seconds (0 = start of the short).

```
valid_start = hook_end + 0.3     # hook_end is 0 if AUTO_HOOK is off, else ~duration of burned hook (~3s) or 3.0 default
valid_end   = clip_duration - ad_duration - 0.4
```

If `valid_end <= valid_start`, **skip the insert** (clip too short for a bumper).

Pick `start` in `[valid_start, valid_end]`:

1. From the clip’s word timestamps, find gaps `end[i] → start[i+1]` ≥ 0.35 s whose gap centre (or start) lies in the valid window. Prefer the longest gap. This uses the transcript we already paid for — **no extra Gemini call in v1**.
2. If none, `random.uniform(valid_start, valid_end)`.

`end = start + ad_duration`.

## Render

Runs **after** watermark, hook, and captions on the file that will be served. Overlay hides burned karaoke for `between(t, start, end)`.

- Scale+cover the ad to the clip’s width×height (typically 1080×1920).
- `overlay` with `enable='between(t,start,end)'`.
- Audio: `volume` duck on `[0:a]` in that window; `atrim` + `adelay` the ad audio; `amix=duration=first`.
- Failure (ffmpeg, missing file): **log and skip**. Never fail the job.

Persist on the clip dict / `*_metadata.json`:

```json
"ad_insert": {
  "start": 12.4,
  "end": 15.1,
  "source_id": "uuid",
  "source_name": "autoaudit-sting.mp4"
}
```

### Recut / restyle (v1 limitation)

Initial process only. Fast recut and `/api/subtitle` restyle **do not** re-apply the bumper.

On-demand insert for **already finished** clips (result-card **add ads** modal) is specified in `2026-09-03-autoaudit-midroll-ondemand-design.md`. That path still does not auto-replay after recut/restyle.

## Library storage

**Not** under a job folder. The janitor deletes `output/<job_id>/` by mtime and would eat ads.

- Directory: `ad_library/` at the app root (next to `output/`). Gitignore the directory.
- `ad_library/manifest.json`:

```json
{
  "active_id": "a1b2c3",
  "items": [
    {"id": "a1b2c3", "filename": "a1b2c3.mp4", "original_name": "autoaudit.mp4", "duration": 3.2}
  ]
}
```

- Activate = set `active_id`. Only one. Activating B deactivates A.
- Delete active → `active_id` becomes `null` (jobs skip insert).
- Max files: 20. Max upload: 20 MB. Extensions: `mp4`, `mov`, `webm`.

v1 is **instance-wide** (one library per server). That matches the PM2 self-host. Per-user libraries wait for a later cloud task.

## Job contract

`POST /api/process` (form + JSON) gains `insert_ad` (`true`/`false`). Default: `true` if the library has `active_id`, else `false`.

`main.py` reads `INSERT_AD=1` from the job env (same pattern as `AUTO_HOOK`). Resolves the active file at render time. If the flag is on but the library is empty, skip.

## API

| Method | Path | Role |
|---|---|---|
| GET | `/api/ad-library` | `{active_id, items: [{id, original_name, duration}]}` |
| POST | `/api/ad-library` | multipart `file` → add item, return it. Does not auto-activate unless this is the first item. |
| POST | `/api/ad-library/{id}/activate` | set `active_id` |
| DELETE | `/api/ad-library/{id}` | remove file + manifest row |

Self-host: no auth (same as the rest of BYOK). Cloud: session user; still one library per instance until per-user storage exists — **do not ship the UI on billing mode** if that would leak one tenant’s ads to another. Gate: `BILLING_ENABLED` → 404 the routes and hide the dashboard block.

## Dashboard

- **Advanced options** on `MediaInput`: checkbox “Sisipkan iklan (AutoAudit)”, default on when `GET /api/ad-library` has `active_id`. Persist `os_insert_ad` in localStorage like `os_auto_hook`.
- Same block: if library empty, short “Upload clip 2–5 detik di library” + file input calling `POST /api/ad-library` then activate.
- If items exist: show active filename, “Ganti yang aktif” (list + activate), upload more.
- Copy: location is automatic (not on the hook); captions hide for those seconds.

## Pipeline position (`main.py`)

Existing order: cut → reframe → watermark → hook grounding → hook burn → captions → `CLIP_READY`.

Insert **after captions**, before `CLIP_READY`, so the overlay covers karaoke.

```
deliver_path = captioned or hooked or clip_final_path
if INSERT_AD: deliver_path = insert_ad_clip(deliver_path, clip, ...)
CLIP_READY uses deliver_path
```

## Non-goals (v1)

- Pexels / generated video
- Multiple inserts
- Per-job file picker beyond on/off
- Extra Gemini call for placement
- Re-applying the bumper after recut/restyle
- Cloud per-user libraries
- Changing SaaSShorts (separate product)

## Success

A 30 s short with an active 3 s AutoAudit file: one full-frame bumper somewhere after the hook, podcast still audible, SFX mixed, no karaoke on the bumper, job succeeds even if ffmpeg overlay fails.
