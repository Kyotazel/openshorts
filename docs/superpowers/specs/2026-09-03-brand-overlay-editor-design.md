# Brand overlay editor (layers + media library)

Date: 2026-09-03
Status: draft — revised after clarification (add layer ≠ replace slot)
Product: OpenShorts self-host (AutoAudit). Cloud (`BILLING_ENABLED`) out of scope for v1 unless enabled later.
Mockup: `docs/superpowers/mockups/2026-09-03-brand-overlay-editor.html`

Depends on / related:
- Mid-roll bumper (`docs/superpowers/specs/2026-09-03-autoaudit-midroll-design.md` + on-demand) stays **separate**. Full-frame 2–5s insert ≠ a positionable overlay layer here.
- Subtitles stay on the existing **subtitles** modal. Not a layer in this editor.
- Free-plan OpenShorts cloud watermark (`assets/watermark.png`) is unrelated to AutoAudit `wm.png`.

## Problem

Operators finish shorts in CapCut by stacking the speaker clip with brand assets (logo, ads MP4, BGM) and sometimes **extra** uploaded media. OpenShorts today has no compositor for that.

They want:

1. New clips to leave the pipeline already branded when a default kit exists.
2. An editor where they can **upload a file → it becomes a new layer on the clip**, move/scale it, place it in time, then burn with ffmpeg.
3. A simple timeline so they can see when each timed layer sits — not a CapCut NLE clone.

## Clarification (locked)

**Upload means “add a layer”, not “replace the file inside an existing WM/ads/BGM row.”**

Wrong (rejected): each of three fixed slots has an Upload that swaps that slot’s file.  
Right: **+ add layer** (or **insert** from library) creates another layer on the clip; the operator can also delete layers. Defaults from the kit are just the starting set of layers.

## Locked decisions

| Topic | Decision |
|---|---|
| Model | Clip has an ordered list of **layers** on top of the locked speaker video. |
| Layer kinds | **image** (png/jpg/webp), **video** (mp4/webm), **audio** (mp3/wav/m4a). No subtitle layers. |
| Add layer | **+ add layer** → file picker → new layer on canvas + timeline track. Also **insert** from media library. |
| Media library | Server-side store of uploaded assets. Insert adds a layer to the **current** clip; does not by itself change other clips. |
| Kit defaults | Marked assets in the library (e.g. `wm.png`, one ads MP4, one BGM) auto-become layers on **new** generated clips. Operator can clear/change which assets are defaults. |
| Per-clip edit | Move/scale visual layers; drag start time on timeline for non-full layers; volume for audio; hide; delete layer. |
| Speaker video | Always full-frame, not movable. Overlays sit **on top**. “Tengah / belakang” = timeline start, not z-order behind the person. |
| Timeline | One locked `video` track + **one track per layer**. Timed overlays = draggable blocks. Full-clip layers (typical WM, BGM) span the whole bar. No razor, no keyframes, no transitions. |
| Stack order | Layer list order = paint order (list top = visually on top). Optional reorder later; v1 can use add-order + delete/re-add. |
| Generate | ffmpeg burns current layer list into a derived file; metadata stores the list. |
| Mid-roll “add ads” | Unchanged, separate button/modal. |
| Recut / subtitle / hook | Do not auto-replay brand layers in v1; re-open editor and generate again if needed. |

## Non-goals (v1)

- CapCut multi-clip NLE (many clips per track, split, transitions)
- Scaling the speaker down to put media “behind” them
- Karaoke as a layer in this editor
- Unlimited cloud hosting of library blobs on `BILLING_ENABLED`
- Per-row “replace file” as the primary upload metaphor

## Architecture

```
media library (disk, self-host)
  manifest.json   # assets + which are kit defaults
  files/…

generate (main.py)
  → seed clip.layers from kit defaults (if any)
  → optional auto-burn with default transforms

Result card → Brand overlay editor
  → layers[] for this clip
  → + add layer / insert from library
  → drag canvas + timeline
  → POST burn → derived mp4 + metadata
```

Authoritative output is **ffmpeg**. Preview (canvas or Remotion) must use the same normalized transform math.

## Media library vs layers

| | Media library | Layers on clip |
|---|---|---|
| What | Reusable files on disk | Instances placed on one clip |
| Upload | “upload to library” (and/or first add-layer also stores the file) | **+ add layer** creates an instance |
| Insert | Button on a library row | Adds another layer instance |
| Kit default | Flag on a library asset | Auto-instantiated when a new clip is produced |

Typical AutoAudit starting kit (library flags):

- `wm.png` → image layer, full clip, top-left ~36%
- `ads-aura.mp4` → video layer, ~mid start, scaled overlay
- `AutoAudit-Whatsapp.mp3` → audio layer, full clip, ducked under podcast / with ads window

Operator can **+ add layer** with another PNG/MP4/MP3; that only affects the current clip unless they also keep it in the library for later inserts.

## Editor UI

Mockup: `docs/superpowers/mockups/2026-09-03-brand-overlay-editor.html`.

1. **Top bar** — reset to kit defaults, close, **generate**
2. **Preview 9:16** — speaker locked; visual layers draggable + scale handle
3. **Layers on clip** — list with **+ add layer**, eye, delete; select drives props
4. **Props** — scale / x / y / start for visuals; volume for audio
5. **Media library** — upload into library; **insert** onto clip; kit-default hint
6. **Timeline** — `video` + one lane per layer; drag timed blocks

## Metadata (illustrative)

```json
{
  "brand_overlay": {
    "layers": [
      {
        "id": "…",
        "asset_id": "…",
        "name": "wm.png",
        "kind": "image",
        "enabled": true,
        "x": 0.06,
        "y": 0.05,
        "scale": 0.36,
        "start": 0,
        "end": null,
        "full": true
      },
      {
        "id": "…",
        "asset_id": "…",
        "name": "ads-aura.mp4",
        "kind": "video",
        "enabled": true,
        "x": 0.08,
        "y": 0.28,
        "scale": 0.84,
        "start": 10.5,
        "duration": 5.4,
        "full": false
      },
      {
        "id": "…",
        "asset_id": "…",
        "name": "AutoAudit-Whatsapp.mp3",
        "kind": "audio",
        "enabled": true,
        "full": true,
        "volume": 0.35,
        "duck": 0.75
      }
    ]
  }
}
```

Burn writes a derived filename (prefix/timestamp); keep a pre-brand master when possible so layers can be re-edited without stacking forever.

## Relation to mid-roll bumper

| | Mid-roll **add ads** | Brand overlay layer |
|---|---|---|
| How added | Dedicated modal / generate flag | **+ add layer** or kit default |
| Geometry | Full-frame fixed | Scale + position |
| Timing | Short mid window | Any start via timeline block |
| Count | One bumper contract today | Many layers |

UI copy must keep the two names distinct (`add ads` vs `brand overlay` / layers).

## Open points

1. Pipeline order vs hook / captions / mid-roll bumper.
2. Auto-burn on generate when kit defaults exist vs editor-only until first generate in the overlay UI.
3. Caps: max layers per clip, max library size / duration / MB.
4. Whether add-layer always copies into the library or can be clip-ephemeral.

## Success criteria

- Operator can upload a new image/video/audio and see it as a **new layer** on the clip, then burn it into the short.
- Kit defaults still seed WM + ads + BGM on new clips without re-uploading.
- Timeline shows when timed layers play; drag changes start.
- Subtitles and mid-roll bumper flows unchanged.
- No CapCut-parity NLE features in v1.
