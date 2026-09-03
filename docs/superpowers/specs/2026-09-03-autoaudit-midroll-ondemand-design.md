# AutoAudit mid-roll on demand (finished clips)

Date: 2026-09-03
Status: approved in conversation
Product: OpenShorts self-host. Cloud (`BILLING_ENABLED`) hides the control and 404s the route, same as the ad library.
Depends on: `docs/superpowers/specs/2026-09-03-autoaudit-midroll-design.md` (overlay contract) and the loudnorm-in-filtergraph fix (FFmpeg 9 rejects `-af` on a `-filter_complex` audio map).

## Problem

Generate-time insert is easy to miss (checkbox in advanced options) and v1 overlay failure left finished shorts without a bumper. The operator wants to add **one** AutoAudit insert to a clip that already exists, from the result card, without re-downloading the source.

## Locked decisions

| Topic | Decision |
|---|---|
| Where | Result card action grid: seventh button **add ads** (new row, still 2 columns). |
| Flow | Same as viral hook: button → modal → edit → **generate** burns. |
| Count | **One bumper per clip.** No second overlay. No per-clip file picker. |
| File | Always the library **active** clip. Duration = whole file, cap 5 s from the start. |
| What is editable | **Start time only** (clip-local seconds). Duration follows the active file. |
| Default start | `pick_ad_window` (pause ≥0.35 s in the valid window, else middle of that window). |
| Valid window | `hook_end + 0.3` … `clip_duration - ad_duration - 0.4`. `hook_end` = 3.0 if the served file name contains `hooked_` or the clip has burned/auto hook, else 0. Skip generate with a clear error if the window does not fit. |
| Picture / audio / captions | Unchanged from the generate-time spec: full-frame overlay, timeline does not grow, podcast continues (duck 0.75 + SFX mix), karaoke hidden because the overlay sits on the already-burned file. |
| Recut / subtitle / hook restyle | Still **do not** auto-replay in this slice. UI copy: after restyle, open add ads and generate again if the bumper is gone. |

## Modal

Open from **add ads**. Compact, no Remotion preview (the bumper is a video overlay, not a text layer).

- Label of the active library clip (`original_name`) and its duration. If the library is empty: short message + no generate (operator uploads from Clip Generator → advanced options).
- Range input for start, clamped to the valid window. Readout `start–end`.
- Primary: **generate**.
- If `clip.ad_insert` is already set: **replace** (same generate, see stacking) and **remove**.

Indonesian copy on the button/modal is fine (`add ads` as the grid label to match `edit clip` / `viral hook` English-lowercase).

## API

`POST /api/ad-insert` (self-host only; `BILLING_ENABLED` → 404).

Body:

```json
{
  "job_id": "...",
  "clip_index": 0,
  "input_filename": "subtitled_…_clip_1.mp4",
  "start": 12.4,
  "remove": false
}
```

- `start` omitted → server runs `pick_ad_window`.
- `start` present → clamp into the valid window; 400 if it cannot fit.
- `remove: true` → stop serving the `adins_` file (see stacking), clear `ad_insert` on the clip, return the parent URL. No ffmpeg.

Success: `{ "new_video_url": "/videos/<job>/<file>", "ad_insert": { ... } }` like `/api/hook`. Overlay failure: 500 with the ffmpeg tail; the previously served file is unchanged.

## Stacking (replace / remove)

Generate-time insert currently overwrites in place. On-demand **must not**: write `adins_<unix>_<basename>` next to the input, same prefix pattern as `hooked_` / `subtitled_`.

- Generate: if `input_filename` already matches `adins_\d+_(.+)`, use the parent file as input when it still exists on disk. Overlay once onto that parent. Never overlay an `adins_` file onto itself.
- Remove: walk `adins_<ts>_` prefixes (same fail-safe as `_strip_burned_hook`). Serve the parent; delete `ad_insert` from metadata.
- Do **not** strip `subtitled_` / `hooked_` first. Captions and hook stay; the bumper sits on top.

Persist `ad_insert` `{ start, end, source_id, source_name }` on the clip in `*_metadata.json`.

## UI wiring

- `ResultCard.jsx`: grid button, modal, POST with `jobId`, `index`, `serverVideoFile`.
- Hide the button when `/api/config.adLibrary` is false or `/api/ad-library` is 404.
- After success, swap `currentVideoUrl` / `serverVideoFile` like hook does.

## Non-goals

- Per-clip library picker
- Batch “all clips in this job”
- Auto-replay after recut / `/api/subtitle` / `/api/hook`
- Changing generate-time in-place overwrite (can align to `adins_` later)
- Cloud per-user libraries
