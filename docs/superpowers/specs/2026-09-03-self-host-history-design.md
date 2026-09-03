# Self-host History tab — design

Date: 2026-09-03
Status: approved in conversation, not implemented
Product: OpenShorts self-host (PM2 / BYOK). Cloud History (`/api/history` + R2 restore) is out of scope.

## Problem

Self-host has no History tab. The Clip Generator only auto-recovers the **newest** job. `/api/jobs` already lists up to 50 jobs from memory and disk, but it does not return the original YouTube (or other) URL, so you cannot find an older generate by the link you pasted.

The operator wants to open previous results from a list keyed by that source URL.

## Locked decisions

| Topic | Decision |
|---|---|
| Purpose | Reopen old clip results, not a batch queue and not published Shorts URLs. |
| Placement | Existing **History** nav tab, un-gated on self-host. |
| Who is listed | Every job `/api/jobs` already returns (URL and upload). YouTube/other URLs shown when present; uploads use the existing display name (filename / first clip title). |
| Click | Load that job into Clip Generator via `GET /api/status/{job_id}` (same path as recovering the newest job). Not cloud `POST /api/projects/{id}/restore`. |
| Clip retention | Self-host default **7 days** (`JOB_RETENTION_SECONDS=604800`). Cloud stays 1 hour. |
| Source file retention | Self-host default **24 hours** (`SOURCE_RETENTION_SECONDS=86400`). Drop the kept YouTube download; leave clips. Recut/reframe that need the original 409 after that, as they do today when the source is gone. |
| Disk cap | Unchanged: `OUTPUT_MAX_GB` (default 25) still purges oldest job dirs if the volume fills inside the week. |
| Cloud | History tab, R2 library, and restore-project stay as they are. |

## What the operator sees

On self-host the History item is always in the nav (today it is `billingEnabled && isSignedIn` only).

The page is a newest-first list, not the cloud clip grid:

- Title: YouTube video title when we have it (yt-dlp / `source_video` basename without extension), else the current `_job_display_name` fallback.
- Subtitle: `source_url` when the job was a URL; omitted for uploads.
- Meta: date, clip count, status.

Click a row → set that job as current, show its clips/logs on Clip Generator. Processing jobs can be opened the same way (live status). A missing/purged job fails the status fetch; show an error on the History row, do not crash.

Copy matches the dashboard: lowercase, mention that clips last 7 days unless `JOB_RETENTION_SECONDS` is raised. The existing dashboard banner that reads `jobRetentionSeconds` from `/api/config` updates itself when the default changes.

## Data

### Persist the source URL

`_job_source_url` already reads `-u` / `--url` from the in-memory `cmd`. That is lost after restart unless we write it to the job dir.

1. At enqueue (`POST /api/process` URL jobs), write `output/<job_id>/.source_url` — one line, the URL. Same idea as `.owner`.
2. When `main.py` writes `*_metadata.json`, also set `source_url` from the `-u` argument (empty/absent for uploads).

History listing reads, in order: in-memory `_job_source_url` → metadata `source_url` → `.source_url` file. Jobs generated before this change have `source_url: null` and still appear by name.

Do not invent a new store or database. Job dirs already are the source of truth until the janitor deletes them.

### `GET /api/jobs`

Each item gains:

```json
{
  "job_id": "...",
  "status": "completed",
  "created": 0,
  "name": "...",
  "clips": 8,
  "source_url": "https://www.youtube.com/watch?v=..." 
}
```

`source_url` is `null` for uploads or unknown. Cap stays 50, newest first. No new list endpoint.

Reopen uses existing `GET /api/status/{job_id}` (disk recovery already rebuilds completed jobs).

## Retention defaults

```python
# self-host
JOB_RETENTION_SECONDS default 604800   # 7 days
SOURCE_RETENTION_SECONDS default 86400 # 24 hours, not copied from the job default

# cloud / BILLING_ENABLED
JOB_RETENTION_SECONDS default 3600
SOURCE_RETENTION_SECONDS default = whatever JOB_RETENTION_SECONDS resolved to
```

Env overrides both. Hosted keeps today's "source clock = job clock" unless `SOURCE_RETENTION_SECONDS` is set. `_sweep_retained_sources` already no-ops unless `SOURCE_RETENTION_SECONDS < JOB_RETENTION_SECONDS`; after this change it will run on self-host by default.

`.env.example` comments must describe these defaults (7-day clips, 24-hour URL originals), not imply both clocks are the same.

Frontend `SESSION_MAX_AGE` (today 24h, documented as matching job retention) becomes 7 days so a browser that still has the session key can restore a job that is still on disk.

## Frontend split

`HistoryTab.jsx` branches:

- `billingEnabled`: current R2 library + reopen via `restoreProject`.
- else: list from `GET /api/jobs`; `onOpenJob(jobId)` loads `/api/status/{jobId}` into App state and `setActiveTab('dashboard')` — extract the same assignment the mount-time “newest job” recovery already does so the two paths cannot drift.

Nav: show History when `!billingEnabled || (billingEnabled && isSignedIn)`.

## Out of scope

- Deduping the same YouTube URL (two generates → two rows).
- Batch paste / queue.
- Published YouTube Shorts links after social upload.
- Changing cloud History or R2 archive lifetime.
- Raising `OUTPUT_MAX_GB`.
- Backfilling `source_url` onto jobs that finished before this ships.

## Tests

- `GET /api/jobs` includes `source_url` from in-memory cmd, from metadata, and from `.source_url` when metadata has no field.
- Upload jobs list `source_url: null`.
- Self-host defaults: job 7d, source 24h; source sweep deletes the kept download and leaves clips.
- History nav renders without billing; cloud gate unchanged when billing is on and the user is signed out.
- Opening a listed job hydrates App from `/api/status` (component or thin handler test).

## Error handling

- Unreadable `.source_url` / metadata: omit `source_url`, still list the job.
- Status 404 (janitor won the race): History shows a short error; stay on the tab.
- Source already swept: clips still play from `/videos/...`; editor endpoints that need the original keep returning 409.
