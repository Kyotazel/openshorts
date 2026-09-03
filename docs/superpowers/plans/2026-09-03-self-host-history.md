# Self-host History Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Self-host operators can open a History tab of past jobs (keyed by YouTube/source URL) and load that job’s clips in Clip Generator.

**Architecture:** Persist each URL job’s source on disk (`.source_url` + `metadata.json`). `GET /api/jobs` returns `source_url`. The existing History nav is un-gated on self-host and lists those jobs; click loads `GET /api/status/{id}` into App (same snapshot as recovering the newest job). Clip retention default becomes 7 days; kept YouTube downloads still age out after 24 hours.

**Tech Stack:** FastAPI (`app.py`), `main.py` metadata, React dashboard (`HistoryTab.jsx`, `App.jsx`), pytest.

## Global Constraints

- Cloud History (`/api/history`, R2, `restoreProject`) is unchanged.
- No new list endpoint; extend `GET /api/jobs` (cap 50).
- `source_url` is `null` for uploads and for jobs that finished before this ships.
- Self-host defaults: `JOB_RETENTION_SECONDS=604800`, `SOURCE_RETENTION_SECONDS=86400` (not copied from the job clock).
- Cloud defaults: job 3600; source still defaults to whatever `JOB_RETENTION_SECONDS` resolved to.
- Dashboard copy stays lowercase.
- `cmd` starts with `[sys.executable, "-u", "main.py"]` then later `-u <url>` — `_job_source_url` must skip the interpreter `-u` or it returns `"main.py"`.

---

### Task 1: Persist and resolve source URL

**Files:**
- Modify: `app.py` (`_job_source_url` ~1146, persist next to `.owner` write ~2547, helpers near `_job_source_url`)
- Modify: `main.py` (~1981, metadata dump)
- Test: `tests/test_job_source_url.py` (new)

**Interfaces:**
- Consumes: job `cmd` list, `OUTPUT_DIR`, job dir path, optional metadata dict
- Produces:
  - `_SOURCE_URL_FILE = ".source_url"`
  - `persist_job_source_url(job_dir: str, url: str | None) -> None`
  - `_job_source_url(job: dict | None) -> str | None` (fixed: URL after `main.py`, not Python `-u`)
  - `listed_source_url(job_id: str, rec: dict | None = None, meta: dict | None = None) -> str | None` — order: cmd, metadata `source_url`, `.source_url` file

- [ ] **Step 1: Write the failing tests**

Create `tests/test_job_source_url.py`:

```python
"""Source URL persistence for the self-host History list."""
import json
import os

import pytest

app_module = pytest.importorskip("app")


def test_job_source_url_skips_python_unbuffered_flag():
    job = {"cmd": ["/usr/bin/python", "-u", "main.py", "-u",
                   "https://www.youtube.com/watch?v=abc", "--keep-original"]}
    assert app_module._job_source_url(job) == "https://www.youtube.com/watch?v=abc"


def test_job_source_url_prefers_long_option():
    job = {"cmd": ["/usr/bin/python", "-u", "main.py", "--url",
                   "https://youtu.be/xyz"]}
    assert app_module._job_source_url(job) == "https://youtu.be/xyz"


def test_job_source_url_none_for_upload_cmd():
    job = {"cmd": ["/usr/bin/python", "-u", "main.py", "-i", "/tmp/x.mp4"]}
    assert app_module._job_source_url(job) is None


def test_persist_and_listed_source_url_from_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    job_id = "job-file"
    d = tmp_path / job_id
    d.mkdir()
    app_module.persist_job_source_url(str(d), "https://www.youtube.com/watch?v=fromfile")
    assert (d / ".source_url").read_text().strip() == "https://www.youtube.com/watch?v=fromfile"
    assert app_module.listed_source_url(job_id) == "https://www.youtube.com/watch?v=fromfile"


def test_listed_source_url_prefers_cmd_then_metadata_then_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    job_id = "job-order"
    d = tmp_path / job_id
    d.mkdir()
    app_module.persist_job_source_url(str(d), "https://example.com/file")
    meta = {"source_url": "https://example.com/meta"}
    rec = {"cmd": ["/usr/bin/python", "-u", "main.py", "-u", "https://example.com/cmd"]}
    assert app_module.listed_source_url(job_id, rec=rec, meta=meta) == "https://example.com/cmd"
    assert app_module.listed_source_url(job_id, rec={}, meta=meta) == "https://example.com/meta"
    assert app_module.listed_source_url(job_id, rec={}, meta={}) == "https://example.com/file"


def test_listed_source_url_unreadable_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    assert app_module.listed_source_url("missing") is None


def test_persist_skips_blank_url(tmp_path):
    d = tmp_path / "job"
    d.mkdir()
    app_module.persist_job_source_url(str(d), "")
    app_module.persist_job_source_url(str(d), None)
    assert not (d / ".source_url").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_job_source_url.py -q`

Expected: FAIL (`persist_job_source_url` / `listed_source_url` missing; `_job_source_url` returns `main.py`)

- [ ] **Step 3: Implement helpers, fix `_job_source_url`, write file on enqueue, set metadata**

In `app.py` next to `_job_source_url`:

```python
_SOURCE_URL_FILE = ".source_url"


def persist_job_source_url(job_dir, url):
    if not url or not str(url).strip():
        return
    try:
        os.makedirs(job_dir, exist_ok=True)
        with open(os.path.join(job_dir, _SOURCE_URL_FILE), "w") as f:
            f.write(str(url).strip() + "\n")
    except Exception as e:
        print(f"⚠️ Could not persist source URL: {e}")


def _job_source_url(job) -> Optional[str]:
    """The URL argument of the job's main.py command, if it was a URL job.

    ``cmd`` is ``[python, -u, main.py, -u, <url>, ...]`` — the first ``-u`` is
    CPython unbuffered, not the video.
    """
    cmd = list((job or {}).get("cmd") or [])
    if "--url" in cmd:
        i = cmd.index("--url")
        val = cmd[i + 1] if i + 1 < len(cmd) else None
        return val if val and not str(val).startswith("-") else None
    try:
        script = cmd.index("main.py")
    except ValueError:
        script = -1
    for i in range(script + 1, len(cmd) - 1):
        if cmd[i] in ("-u", "--url"):
            val = cmd[i + 1]
            if val and not str(val).startswith("-"):
                return val
    return None


def listed_source_url(job_id, rec=None, meta=None):
    from_cmd = _job_source_url(rec)
    if from_cmd:
        return from_cmd
    if isinstance(meta, dict):
        raw = meta.get("source_url")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    path = os.path.join(OUTPUT_DIR, job_id, _SOURCE_URL_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
        return raw or None
    except OSError:
        return None
```

After `os.makedirs(job_output_dir, exist_ok=True)` in `process_video`, if `url:` call `persist_job_source_url(job_output_dir, url)`.

In `main.py` when writing metadata (after `clips_data['source_video'] = ...`):

```python
            if args.url:
                clips_data["source_url"] = args.url
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_job_source_url.py -q`

Expected: PASS

- [ ] **Step 5: Commit** (batch with later tasks if implementing in one session)

---

### Task 2: `GET /api/jobs` returns `source_url`

**Files:**
- Modify: `app.py` `list_jobs` (~2637–2699) and `_job_display_name` (~2621)
- Test: `tests/test_job_source_url.py` (add HTTP cases)

**Interfaces:**
- Consumes: `listed_source_url`, `_job_display_name`
- Produces: each jobs item includes `"source_url": str | null`. For `source_video` names, strip `.mp4`/`.webm`/`.mkv`/`.mov`.

- [ ] **Step 1: Write the failing HTTP tests**

Append to `tests/test_job_source_url.py` (ASGI pattern from `tests/test_source_access.py`):

```python
import asyncio
import httpx


def _get(path):
    async def _do():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            return await client.get(path)
    return asyncio.run(_do())


def test_api_jobs_includes_source_url_from_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    job_id = "mem-url-job"
    (tmp_path / job_id).mkdir()
    app_module.jobs[job_id] = {
        "status": "completed",
        "logs": [],
        "cmd": ["/usr/bin/python", "-u", "main.py", "-u",
                "https://www.youtube.com/watch?v=mem", "--keep-original"],
        "result": {"clips": [{"title": "A"}]},
        "created_at": 1,
    }
    try:
        r = _get("/api/jobs")
        assert r.status_code == 200
        row = next(j for j in r.json()["jobs"] if j["job_id"] == job_id)
        assert row["source_url"] == "https://www.youtube.com/watch?v=mem"
        assert row["clips"] == 1
    finally:
        app_module.jobs.pop(job_id, None)


def test_api_jobs_disk_job_reads_metadata_then_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    job_id = "disk-url-job"
    d = tmp_path / job_id
    d.mkdir()
    (d / "Talk_Title_metadata.json").write_text(json.dumps({
        "source_video": "Talk_Title.mp4",
        "source_url": "https://www.youtube.com/watch?v=disk",
        "shorts": [{}, {}],
    }))
    r = _get("/api/jobs")
    row = next(j for j in r.json()["jobs"] if j["job_id"] == job_id)
    assert row["source_url"] == "https://www.youtube.com/watch?v=disk"
    assert row["name"] == "Talk_Title"
    assert row["clips"] == 2


def test_api_jobs_upload_has_null_source_url(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    job_id = "upload-job"
    app_module.jobs[job_id] = {
        "status": "completed",
        "logs": [],
        "cmd": ["/usr/bin/python", "-u", "main.py", "-i", "/tmp/a.mp4"],
        "result": {"clips": []},
        "created_at": 2,
    }
    (tmp_path / job_id).mkdir()
    try:
        r = _get("/api/jobs")
        row = next(j for j in r.json()["jobs"] if j["job_id"] == job_id)
        assert row["source_url"] is None
    finally:
        app_module.jobs.pop(job_id, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_job_source_url.py -q`

Expected: FAIL (`source_url` KeyError / name still `Talk_Title.mp4`)

- [ ] **Step 3: Wire `list_jobs` and strip source_video extension**

`_job_display_name`: when the winning key is `source_video`, `os.path.splitext` and drop video suffixes.

Both `items.append` dicts in `list_jobs` add:

```python
"source_url": listed_source_url(job_id, rec=rec, meta=None),  # in-memory loop
# disk loop:
"source_url": listed_source_url(entry, rec=None, meta=meta),
```

Use the in-memory `job_id` / disk `entry` as the id argument.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_job_source_url.py tests/test_source_access.py -q`

Expected: PASS

---

### Task 3: Retention defaults

**Files:**
- Modify: `app.py` ~101–114
- Modify: `.env.example` ~87–91
- Modify: `dashboard/src/App.jsx` `SESSION_MAX_AGE` (~189–191)
- Test: `tests/test_job_source_url.py` + existing `tests/test_source_access.py::TestRetainedSourceSweep`

**Interfaces:**
- Produces: self-host import (conftest `BILLING_ENABLED=0`) has `JOB_RETENTION_SECONDS == 604800` and `SOURCE_RETENTION_SECONDS == 86400`

- [ ] **Step 1: Write the failing default test**

```python
def test_self_host_retention_defaults():
    assert app_module.BILLING_ENABLED is False
    assert app_module.JOB_RETENTION_SECONDS == 604800
    assert app_module.SOURCE_RETENTION_SECONDS == 86400
    assert app_module.SOURCE_RETENTION_SECONDS < app_module.JOB_RETENTION_SECONDS
```

- [ ] **Step 2: Run it**

Expected: FAIL (86400 == 86400 for both)

- [ ] **Step 3: Change defaults**

```python
JOB_RETENTION_SECONDS = int(
    os.environ.get("JOB_RETENTION_SECONDS", "3600" if BILLING_ENABLED else "604800")
)
SOURCE_RETENTION_SECONDS = int(
    os.environ.get(
        "SOURCE_RETENTION_SECONDS",
        str(JOB_RETENTION_SECONDS) if BILLING_ENABLED else "86400",
    )
)
```

Update the comment above those lines (7-day clips, 24-hour URL originals on self-host).

`.env.example`:

```
# How long job clips stay on disk before the janitor deletes them.
# Default: 604800 (7 days) self-host, 3600 hosted.
# JOB_RETENTION_SECONDS=604800
# Kept YouTube downloads (--keep-original) can age out sooner than clips.
# Default: 86400 (1 day) self-host; hosted follows JOB_RETENTION_SECONDS.
# SOURCE_RETENTION_SECONDS=86400
```

`App.jsx`: `const SESSION_MAX_AGE = 7 * 86400000;` and comment that it matches the 7-day job default.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_job_source_url.py tests/test_source_access.py::TestRetainedSourceSweep -q`

Expected: PASS (`test_is_a_no_op_at_the_default` still monkeypatches SOURCE=JOB)

---

### Task 4: History tab (self-host list + open job)

**Files:**
- Modify: `dashboard/src/components/HistoryTab.jsx`
- Modify: `dashboard/src/App.jsx` (nav ~996, recovery ~574–602, History view ~1707–1714, `SESSION_MAX_AGE` already task 3)
- Test: `tests/test_job_source_url.py` for `historyNavVisible` is frontend — dashboard has no test runner. Export a tiny helper from `dashboard/src/lib/historyNav.js` and do not add a JS test harness. Verify nav condition by inspection + grep in this task. Opening a job is the same `applyJobSnapshot` used by server recovery.

**Interfaces:**
- Consumes: `GET /api/jobs` `{ jobs: [{ job_id, status, created, name, clips, source_url }] }`
- Produces:
  - `historyNavVisible(billingEnabled, isSignedIn) => !billingEnabled || !!isSignedIn`
  - `HistoryTab({ billingEnabled, onReopenProject, onOpenJob })`
  - `applyJobSnapshot(jobId, data)` in App

- [ ] **Step 1: Add `dashboard/src/lib/historyNav.js`**

```javascript
export function historyNavVisible(billingEnabled, isSignedIn) {
  return !billingEnabled || !!isSignedIn;
}
```

- [ ] **Step 2: Branch `HistoryTab.jsx`**

If `!billingEnabled`, fetch `/api/jobs`, render a newest-first list (API already sorts). Each row: `name`, `source_url` as a muted subtitle (omit if null), date from `created` (unix seconds or ms — `created` from in-memory may be unix, from disk `getmtime` is unix seconds), clip count, status. Click calls `onOpenJob(jobId)`. On throw, set row/page error, stay on the tab. Empty: “no jobs yet. generate your first short from the clip generator.” Intro copy: clips are kept for 7 days unless `JOB_RETENTION_SECONDS` is raised.

If `billingEnabled`, keep the current R2 library UI and `onReopenProject`.

`created` formatting: `new Date((created > 1e12 ? created : created * 1000))` so both ms and seconds work.

- [ ] **Step 3: Wire `App.jsx`**

Extract from the mount recovery effect:

```javascript
  const applyJobSnapshot = (id, data) => {
    setJobId(id);
    setResults(data.result || null);
    setProcessingMedia({ type: 'server', payload: `/api/source/${id}` });
    setNoSource(false);
    setQualityGate(null);
    if (data.status === 'completed' || data.status === 'complete') setStatus('complete');
    else if (data.status === 'failed' || data.status === 'error') setStatus('error');
    else setStatus('processing');
    setLogs(Array.isArray(data.logs) ? data.logs : []);
    setActiveTab('dashboard');
  };

  const openLocalJob = async (id) => {
    const data = await apiJson(`/api/status/${id}`);
    applyJobSnapshot(id, data);
  };
```

Recovery effect uses `applyJobSnapshot(top.job_id, data)` instead of duplicating assignments.

Nav:

```javascript
import { historyNavVisible } from './lib/historyNav';
// ...
...(historyNavVisible(billingEnabled, isSignedIn) ? [{ id: 'history', ord: '06', icon: History, label: 'History', short: 'history' }] : []),
```

History view:

```javascript
<HistoryTab
  billingEnabled={billingEnabled}
  onReopenProject={restoreProject}
  onOpenJob={openLocalJob}
/>
```

- [ ] **Step 4: Grep + pytest**

Run: `python3 -m pytest tests/test_job_source_url.py tests/test_source_access.py -q`

Grep: `historyNavVisible` used in `App.jsx`; `billingEnabled && isSignedIn ? [{ id: 'history'` is gone.

- [ ] **Step 5: Commit backend + dashboard together**

```
feat(dashboard): self-host History tab to reopen jobs by source URL
```

---

## Spec coverage

| Spec item | Task |
|---|---|
| Persist `.source_url` at enqueue | 1 |
| `metadata.json` `source_url` | 1 |
| Fix cmd `-u` collision | 1 |
| `GET /api/jobs` `source_url` | 2 |
| Display name from source_video stem | 2 |
| 7-day job / 24h source defaults | 3 |
| `.env.example` | 3 |
| `SESSION_MAX_AGE` 7 days | 3 |
| History nav on self-host | 4 |
| Cloud History unchanged | 4 (`billingEnabled` branch) |
| Click → `/api/status` into dashboard | 4 |
| 404 stays on History tab | 4 |
| No new list endpoint / no R2 / no dedupe / no batch | — out of scope, not tasked |
