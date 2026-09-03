# AutoAudit on-demand insert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an **add ads** control on the result card that burns one active-library bumper onto an already-finished clip.

**Architecture:** Reuse `ad_insert.build_overlay_cmd` / `pick_ad_window`. On-demand writes `adins_<unix>_<basename>` (never overlay an `adins_` file onto itself). `POST /api/ad-insert` mirrors `/api/hook`. Loudnorm stays inside the filtergraph (no `-af`).

**Tech Stack:** FastAPI, ffmpeg, React (`ResultCard`, new modal).

## Global Constraints

- One bumper per clip; always the library active file; start time is the only edit.
- Self-host only: `BILLING_ENABLED` → 404; hide the button.
- Overlay sits on the current served file (keep hook + captions).
- Recut/subtitle/hook restyle do not auto-replay.
- Overlay failure → 500; previous file unchanged.

---

### Task 1: Derived-file burn + strip

**Files:**
- Modify: `ad_insert.py`
- Modify: `tests/test_ad_insert.py`

**Interfaces:**
- `strip_adins_name(output_dir, filename) -> str` — walk `adins_<digits>_` while parent exists.
- `burn_ad_derived(video_path, ad_path, start, ad_duration, ...) -> out_path` — writes `adins_<ts>_<base>`, does **not** `os.replace`; raises on ffmpeg failure.
- `insert_ad_clip` stays in-place for generate-time (spec non-goal to change).

---

### Task 2: HTTP API

**Files:**
- Modify: `app.py`
- Create: `tests/test_ad_insert_api.py` (skip if no app)

**Interfaces:**
- `GET /api/ad-insert/plan?job_id&clip_index&input_filename` → suggested `{start,end,valid_start,valid_end,ad_duration,source_name,ad_insert}`
- `POST /api/ad-insert` `{job_id, clip_index, input_filename, start?, remove?}` → `{new_video_url, ad_insert}`

---

### Task 3: Result card UI

**Files:**
- Create: `dashboard/src/components/AdInsertModal.jsx`
- Modify: `dashboard/src/components/ResultCard.jsx`

Grid button **add ads**; modal range for start; generate / replace / remove.
