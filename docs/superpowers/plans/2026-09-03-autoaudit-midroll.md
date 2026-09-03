# AutoAudit Mid-Roll Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert one 2–5 s operator-uploaded AutoAudit bumper into each short after the hook, with podcast audio ducked and karaoke hidden, defaulting to the single active library clip.

**Architecture:** Disk library (`ad_library/manifest.json` + media files) outside job output. Pure helpers pick a start time from transcript gaps (else random). FFmpeg overlay+amix runs after captions in `main.py`. Dashboard advanced options toggle the job flag and manage the one active clip.

**Tech Stack:** FastAPI, ffmpeg, React (`MediaInput.jsx`), pytest. No new Python dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-03-autoaudit-midroll-design.md`
- Do not store the library under `output/<job_id>/` (janitor deletes it).
- `BILLING_ENABLED`: ad-library routes return 404; dashboard block hidden (instance-wide library would leak across tenants).
- Overlay failure must not fail the job.
- TDD: failing test before production code. Do not commit unless the user asks.
- v1: one insert, one active clip, no recut/restyle replay, no extra Gemini call.

## File map

| File | Role |
|---|---|
| `ad_library.py` | Manifest CRUD, duration probe, active file path |
| `ad_insert.py` | `pick_ad_window`, `overlay_ad_ffmpeg`, `insert_ad_clip` |
| `tests/test_ad_library.py` | Manifest activate/delete/first-item |
| `tests/test_ad_insert.py` | Window picker + ffmpeg filter graph (no real encode required for picker tests) |
| `app.py` | `/api/ad-library*` + `insert_ad` on `/api/process` → `INSERT_AD` env |
| `main.py` | After `auto_caption_clip`, call `insert_ad_clip` |
| `dashboard/src/components/MediaInput.jsx` | Checkbox + mini library UI |
| `.gitignore` | `ad_library/` |

---

### Task 1: Library on disk

**Files:**
- Create: `ad_library.py`
- Create: `tests/test_ad_library.py`
- Modify: `.gitignore` (add `ad_library/`)

**Interfaces:**
- Produces: `library_dir() -> str`, `read_manifest() -> dict`, `add_item(src_path, original_name) -> dict`, `activate(id) -> dict`, `delete_item(id) -> dict`, `active_path() -> str | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ad_library.py
import json
import os
import ad_library as lib

def test_first_upload_becomes_active(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "library_dir", lambda: str(tmp_path))
    src = tmp_path / "in.mp4"
    src.write_bytes(b"not-a-real-mp4")
    monkeypatch.setattr(lib, "probe_duration", lambda path: 3.2)
    item = lib.add_item(str(src), "autoaudit.mp4")
    man = lib.read_manifest()
    assert man["active_id"] == item["id"]
    assert item["duration"] == 3.2
    assert os.path.isfile(os.path.join(str(tmp_path), item["filename"]))

def test_second_upload_does_not_steal_active(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "library_dir", lambda: str(tmp_path))
    monkeypatch.setattr(lib, "probe_duration", lambda path: 2.0)
    a = lib.add_item(str(tmp_path / "a.mp4"), "a.mp4")
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    # add_item copies from src; create files first
    ...
```

Keep the second test fully written: two `write_bytes` files, `add_item` twice, assert `active_id` stays the first id; `activate(second)` then flips it. `delete_item(active)` sets `active_id` to `None`. Reject `original_name` with no video extension.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ad_library.py -q --tb=short`  
Expected: collection error `No module named 'ad_library'` (or import error).

- [ ] **Step 3: Write minimal implementation**

`library_dir()` = `os.environ.get("AD_LIBRARY_DIR")` or `<repo>/ad_library`. Manifest default `{"active_id": None, "items": []}`. `add_item` copies to `{id}.mp4` (uuid hex 8+), probes duration via ffprobe (injectable `probe_duration` for tests), appends, sets `active_id` only when it was `None`. `activate` raises `KeyError` on unknown id. Cap 20 items (`LibraryFull`).

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_ad_library.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit only if the user asked**

---

### Task 2: Placement picker

**Files:**
- Create: `ad_insert.py` (picker only in this task)
- Create: `tests/test_ad_insert.py`

**Interfaces:**
- Produces: `pick_ad_window(clip_duration, ad_duration, words, hook_end=3.0) -> tuple[float, float] | None`
- `words` is `[{"start": float, "end": float}, ...]` in **clip-local** seconds.

- [ ] **Step 1: Write the failing test**

```python
import ad_insert as ad

def test_skip_when_clip_too_short():
    assert ad.pick_ad_window(8.0, 4.0, [], hook_end=3.0) is None

def test_prefers_longest_gap_in_valid_window():
    words = [
        {"start": 3.5, "end": 4.0},
        {"start": 4.1, "end": 5.0},   # 0.1s gap
        {"start": 8.0, "end": 9.0},   # 3.0s gap — winner
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
```

- [ ] **Step 2: Run to see FAIL** (`No module named 'ad_insert'` or `pick_ad_window` missing).

- [ ] **Step 3: Implement `pick_ad_window`**

```python
def pick_ad_window(clip_duration, ad_duration, words, hook_end=3.0):
    valid_start = float(hook_end) + 0.3
    valid_end = float(clip_duration) - float(ad_duration) - 0.4
    if valid_end <= valid_start:
        return None
    best = None
    seq = sorted(words or [], key=lambda w: w["start"])
    for i in range(len(seq) - 1):
        gap0, gap1 = seq[i]["end"], seq[i + 1]["start"]
        gap = gap1 - gap0
        if gap < 0.35:
            continue
        # place ad start at gap start, clamped into the valid window
        cand = min(max(gap0, valid_start), valid_end)
        if valid_start <= cand <= valid_end and (best is None or gap > best[0]):
            best = (gap, cand)
    start = best[1] if best else random.uniform(valid_start, valid_end)
    return (float(start), float(start) + float(ad_duration))
```

- [ ] **Step 4: pytest `tests/test_ad_insert.py` — PASS**

---

### Task 3: FFmpeg overlay helper

**Files:**
- Modify: `ad_insert.py`
- Modify: `tests/test_ad_insert.py`

**Interfaces:**
- Produces: `build_overlay_cmd(main_path, ad_path, out_path, start, ad_duration, width, height, duck=0.75, has_ad_audio=True) -> list[str]`
- Produces: `insert_ad_clip(video_path, ad_path, start, ad_duration, probe_size, run_ffmpeg) -> str` (returns `video_path` on failure)

- [ ] **Step 1: Test the filter graph, not a real encode**

```python
def test_overlay_enable_uses_window():
    cmd = ad.build_overlay_cmd("a.mp4", "b.mp4", "c.mp4",
                               start=12.4, ad_duration=3.0,
                               width=1080, height=1920)
    joined = " ".join(cmd)
    assert "between(t,12.4" in joined
    assert "volume=0.75" in joined or "volume='0.75'" in joined or "volume=enable" in joined
    assert "amix" in joined
    assert "scale=1080:1920" in joined

def test_no_ad_audio_skips_amix():
    cmd = ad.build_overlay_cmd("a.mp4", "b.mp4", "c.mp4",
                               start=10, ad_duration=2,
                               width=1080, height=1920, has_ad_audio=False)
    assert "amix" not in " ".join(cmd)
```

Filter sketch (implement exactly this shape):

```
[1:v]scale=W:H:force_original_aspect_ratio=increase,crop=W:H,setsar=1[ad];
[0:v][ad]overlay=0:0:enable='between(t,START,END)'[v];
[0:a]volume=enable='between(t,START,END)':volume=0.75[a0];
[1:a]atrim=0:AD,asetpts=PTS-STARTPTS,adelay=MS|MS[a1];
[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[a]
```

`MS = int(start * 1000)`. `END = start + ad_duration`.

`insert_ad_clip`: write next to the source as `adins_<basename>`, replace on success; `except: print warning; return video_path`.

- [ ] **Step 2–4:** red, implement, green.

---

### Task 4: Wire the pipeline

**Files:**
- Modify: `main.py` (clip loop after `auto_caption_clip`, ~2025–2035)
- Modify: `tests/test_clip_ready_marker.py` if it asserts exact call order — extend so `insert_ad_clip` sits after captions and before `CLIP_READY`.

**Interfaces:**
- Consumes: `ad_library.active_path()`, `ad_insert.pick_ad_window`, `ad_insert.insert_ad_clip`
- `INSERT_AD=1` env, same style as `AUTO_HOOK`

- [ ] **Step 1:** In `test_clip_ready_marker.py`, assert `insert_ad_clip` (or the env gate) appears after `auto_caption_clip` and before `CLIP_READY` in `main.py` source. Watch it fail.

- [ ] **Step 2:** After captions:

```python
if success and os.environ.get("INSERT_AD", "").strip() == "1":
    import ad_library as _ads
    import ad_insert as _adins
    ad_path = _ads.active_path()
    if ad_path:
        clip_dur = end - start
        ad_dur = _ads.probe_duration(ad_path) or 3.0
        if ad_dur > 5.0:
            ad_dur = 5.0
        hook_end = 3.0 if os.environ.get("AUTO_HOOK") == "1" else 0.0
        words = []
        for seg in (transcript or {}).get("segments") or []:
            for w in seg.get("words") or []:
                ws, we = w.get("start"), w.get("end")
                if ws is None or we is None:
                    continue
                if we < start or ws > end:
                    continue
                words.append({"start": float(ws) - start, "end": float(we) - start})
        window = _adins.pick_ad_window(clip_dur, ad_dur, words, hook_end=hook_end)
        if window:
            deliver_path = captioned or deliver_path
            new_path = _adins.insert_ad_clip(deliver_path, ad_path, window[0], ad_dur)
            if new_path:
                deliver_path = new_path
                clip["ad_insert"] = {
                    "start": window[0], "end": window[1],
                    "source_id": os.path.splitext(os.path.basename(ad_path))[0],
                    "source_name": os.path.basename(ad_path),
                }
```

Pass `deliver_path` into `CLIP_READY` the same way captions already do (see existing `ready_files` / print). Read that block and keep its contract.

- [ ] **Step 3:** pytest `tests/test_clip_ready_marker.py` + `tests/test_ad_insert.py` PASS.

---

### Task 5: HTTP API + process flag

**Files:**
- Modify: `app.py` (`Process` form fields ~2145, JSON body parse ~2186, `layout_env`-style env for the job)
- Create: `tests/test_ad_library_api.py` (httpx `TestClient`, skip if import of `app` is too heavy — follow `tests/test_short_source_gate.py` pattern)

**Interfaces:**
- `GET/POST /api/ad-library`, `POST /api/ad-library/{id}/activate`, `DELETE /api/ad-library/{id}`
- Form/JSON `insert_ad`; job env `INSERT_AD=1|0`
- If `BILLING_ENABLED`: those four routes 404

- [ ] **Step 1:** Test `layout_env`-equivalent: a helper `ad_insert_env(insert_ad_flag, has_active) -> dict` in `app.py` or `ad_library.py`:

```python
def insert_ad_env(requested, has_active):
    if requested is False:
        return {"INSERT_AD": "0"}
    if requested is True or (requested is None and has_active):
        return {"INSERT_AD": "1"}
    return {"INSERT_AD": "0"}
```

Tests: `None + active → 1`, `None + empty → 0`, `False + active → 0`, `True + empty → 1` (render will skip missing file).

- [ ] **Step 2:** Implement routes using `ad_library.*`. Upload via `UploadFile`, save to a temp path, `add_item`. First item activates (already in Task 1).

- [ ] **Step 3:** Thread `insert_ad` into the existing `/api/process` env dict next to `AUTO_HOOK`.

- [ ] **Step 4:** pytest the new unit tests. App import tests only if the file already imports `app` that way.

---

### Task 6: Dashboard

**Files:**
- Modify: `dashboard/src/components/MediaInput.jsx`
- Modify: `dashboard/src/App.jsx` (pass `insert_ad` into `onProcess` / FormData — find the existing `auto_hook` append and copy that pattern)

**UI copy (Indonesian, short):**
- Checkbox: `Sisipkan iklan AutoAudit`
- Hint: `1 clip 2–5 dtk setelah hook. Suara podcast tetap; subtitle disembunyikan di jendela itu.`
- Empty library: file input `Upload clip iklan (2–5 detik)`
- Active: `Aktif: {original_name}` + `Aktifkan` on other rows

- [ ] **Step 1:** Default checkbox from `localStorage.os_insert_ad` unless `'0'`; after mount `GET /api/ad-library` — if no `active_id`, checkbox off.
- [ ] **Step 2:** Submit includes `insert_ad: 'true'|'false'` in FormData / JSON like `auto_hook`.
- [ ] **Step 3:** Hide the whole block when `/api/config` (or the library GET 404) says billing/cloud. If `GET /api/ad-library` is 404, render nothing.
- [ ] **Step 4:** `npm run lint` in `dashboard/` (`--max-warnings 0`).

No new npm packages.

---

### Task 7: Janitor + gitignore + docs surface

**Files:**
- Modify: `.gitignore` (`ad_library/`)
- Modify: `app.py` cleanup **only if** you placed the library under `OUTPUT_DIR` — do not. Confirm `library_dir()` is **not** inside `OUTPUT_DIR`.
- Modify: `.env.example` optional `AD_LIBRARY_DIR=` comment.

- [ ] **Step 1:** Test that `library_dir()` default path’s parent is the repo root, not `output/`.
- [ ] **Step 2:** gitignore.
- [ ] **Step 3:** Run `python3 -m pytest tests/test_ad_library.py tests/test_ad_insert.py tests/test_clip_ready_marker.py tests/test_download_format.py -q`

---

## Spec coverage

| Spec item | Task |
|---|---|
| Disk library, one active | 1 |
| Pause then random placement | 2 |
| Overlay + duck + SFX mix; skip if no ad audio | 3 |
| After captions, skip-on-failure | 4 |
| `insert_ad` default = active exists | 5 |
| Dashboard checkbox + upload | 6 |
| Not in `output/<job>` | 1, 7 |
| BILLING 404 | 5, 6 |
| No recut replay | 4 (don’t touch `recut.py`) |
| Use file whole, cap 5 s | 4 |

## Out of scope (do not implement in this plan)

Per-job file picker, Gemini placement call, recut/restyle replay, Pexels, cloud per-user library, SaaSShorts.
