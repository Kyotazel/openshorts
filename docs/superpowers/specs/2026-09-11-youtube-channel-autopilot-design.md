# YouTube channel autopilot — design

Date: 2026-09-11
Status: approved and implemented (2026-09-11)
Product: OpenShorts self-host (PM2, single fork-mode process). Cloud multi-user is out of scope for v1.

## Problem

OpenShorts is fully manual today: a human pastes a YouTube URL, waits, then
downloads clips. There is no way to say "watch these channels, and every day at
08:00 Jakarta turn whatever they published into clips and hand me a ZIP".

This adds a subscription layer on top of the existing pipeline:

1. Operator adds one or more YouTube channels in the dashboard.
2. A new upload on any channel is detected and parked as *pending*.
3. Once a day, at a configured hour (Asia/Jakarta), every pending video is
   submitted as a normal OpenShorts job.
4. When each job finishes, its clips are packed into one ZIP per video and
   POSTed (multipart) to a configured target API.
5. Everything appears in History like a hand-submitted job.

## Locked decisions

| Topic | Decision |
|---|---|
| Delivery format | Build ZIP, **POST multipart** to the target API. |
| Mode | Self-host only (`BILLING_ENABLED=0`). No metering, no DB. |
| Deploy | PM2 fork-mode, one process. No cross-instance coordination needed. |
| Detection | YouTube **WebSub (PubSubHubbub) push** as primary, **RSS poll** as fallback. App is publicly reachable. |
| First subscribe | Only videos published **after** the channel was added. No backfill. |
| Schedule | **One global hour**, timezone Asia/Jakarta, configurable in the dashboard. |
| Recipe | Same pipeline options for every channel; no per-channel overrides. |
| Captions | On by default (burned subtitles). The TikTok/IG caption **text is included in the ZIP**. |
| ZIP granularity | **One ZIP per video** (= one ZIP per job). |
| Delivery auth | Configurable **multiple custom headers** + optional HMAC secret. |
| Retry | 3 automatic attempts, plus **manual re-send from History**. |
| Notifications | Out of scope for v1; surface state in History/job logs. |

## Detection

### Channel resolution

The operator pastes a channel URL or `@handle`. Resolve to a channel id once and
store it:

- `/channel/UC...` → use the id directly, no fetch.
- `/@handle`, `/c/...`, `/user/...` → fetch the channel page and read
  `"externalId":"UC..."` (fallbacks: `<meta itemprop="channelId">`,
  `og:title` for the display name).

### WebSub (primary)

- Hub: `https://pubsubhubbub.appspot.com/subscribe`
- Topic: `https://www.youtube.com/xml/feeds/videos.xml?channel_id=UC...`
- Callback: `{PUBLIC_API_URL}/api/automation/youtube/callback`

Subscribe with form fields `hub.mode=subscribe`, `hub.topic`,
`hub.callback`, `hub.verify=async`, `hub.lease_seconds=432000` (5 days; the
hub may shorten it — persist whatever it returns).

Two callback methods on the same path:

- **GET** (verification): echo `hub.challenge` as `text/plain`, HTTP 200.
- **POST** (notification): parse the Atom body for `<yt:videoId>`,
  `<yt:channelId>`, `<title>`, `<published>`, `<link>`; respond 200 fast.
  `hub.mode=denied` is logged.

A lease-renewal loop runs every 12 h (and at startup): any subscription whose
lease expires within 24 h is re-subscribed.

### RSS fallback

Every 30 min, fetch `https://www.youtube.com/feeds/videos.xml?channel_id=UC...`
for every active channel and merge entries into pending. This covers WebSub's
known gaps (delayed or dropped pushes) and makes the feature work even if the
hub is unhappy. Dedup by `video_id`, so push + poll never double-queue.

Only entries whose `published` is >= the subscription's `created_at` are
accepted, which enforces "new videos after subscribe".

Live streams, premieres and not-yet-processed uploads are handled at submit
time by the existing quality gate (see Retry).

## Storage

**Not** under `output/`: the janitor deletes `output/<job_id>/` by mtime and
would eat the queue. Follow the `ad_library/` precedent and use a top-level
directory, gitignored.

```
automation/
  settings.json        # enabled, run_hour, timezone, delivery {url, headers, secret, file_field}
  subscriptions.json   # [{id, channel_id, title, handle, lease_expires_at, created_at, status}]
  pending.json         # [{video_id, channel_id, channel_title, title, url, published,
                        #   status, attempts, next_attempt_at, job_id, last_error}]
  schedule.json        # {"last_run_date": "2026-09-11"}
  outbox/<job_id>.json # delivery state + frozen target config
  outbox/<job_id>.zip  # ZIP awaiting delivery
```

All writes are atomic (`.tmp` + `os.replace`) under a single `threading.Lock`.
A corrupt or unreadable file is moved aside and rebuilt rather than crashing
startup.

## Scheduler

One loop, tick every 30 s:

1. If `enabled` and local (Asia/Jakarta) time has passed `run_hour` and
   `schedule.last_run_date != today`: run the **daily pass**.
2. Daily pass: for every pending item with status `new`, call the submit
   helper. Each success flips the item to `queued` and records its `job_id`.
   The pass is idempotent — already-`queued` items are skipped — and
   `last_run_date` is written only **after** the pass, so a PM2 restart
   mid-pass resumes the remaining items the same day.
3. A **retry sweep** runs the same tick: any item with status `retry` and
   `next_attempt_at <= now` is submitted again (up to 3 attempts). Retries are
   allowed any time of day, because they are recovering an already-due item,
   not discovering a new one.

Pending statuses: `new` → (daily pass) → `queued` → `done`; or
`retry` → `queued`; or `failed` after 3 attempts; `skip` for permanently
ineligible videos.

### Submit outcome handling

The existing quality gate returns `needs_confirmation` when a fresh upload is
only available below 720p, and rejects sources shorter than
`MIN_SOURCE_SECONDS`. The automation cannot answer a confirmation, so:

- `needs_confirmation` / transient download error → status `retry`,
  `next_attempt_at = now + 30 min`, `attempts += 1`.
- Hard rejection (too short, private, removed) → `skip` with `last_error`.
- 3 failed attempts → `failed`, visible in the dashboard.

## Job submission (refactor)

`process_endpoint` is one 380-line function. Extract its tail — everything from
building `cmd`/`env` through creating the `jobs[job_id]` record, writing the
resume manifest and calling `_enqueue_job` — into an internal
`enqueue_process_job(...)`. `process_endpoint` keeps all auth/form/probe work
and calls it at the end; the automation calls the same helper with a stored
recipe. This is what makes automation jobs inherit, for free:

- the resume manifest + `.transcript_checkpoint.json` (survives PM2 restart
  without re-paying transcription),
- the `.owner`/job-dir layout and History visibility,
- the existing concurrency semaphore and priority queue.

The helper takes: source url, output format, layouts, captions, auto_hook,
auto_hook_style, target_clips, clip min/max, insert_ad. v1 recipe defaults:
captions on, layouts from the deployment env, auto-hook as today.

To mark a job as automation-owned, add `automation` metadata to the job record
and to the resume manifest: `{video_id, channel_id, channel_title, source_url}`.
That is what the delivery stage reads.

## Delivery

Hooked into `run_job_wrapper`'s `finally`, after the job is terminal. Only for
jobs carrying the `automation` marker.

### ZIP contents

One ZIP per job:

```
clip_01_<file>.mp4
clip_02_<file>.mp4
captions.json     # per clip: index, title (YouTube Short), tiktok, instagram, hook
metadata.json     # the job's *_metadata.json (optional but useful)
```

`captions.json` closes the loop on the requirement: the TikTok/IG caption the
dashboard shows (`video_description_for_tiktok`,
`video_description_for_instagram`) plus `video_title_for_youtube_short` and
`viral_hook_text`.

Built with `ZIP_STORED` (reuse the `/api/jobs/{id}/download-all` logic, which
is already extracted into a helper as part of this work) into
`automation/outbox/<job_id>.zip`.

### POST

`httpx` streams the file from disk (never loads it in memory):

```python
files = {settings.file_field or "file": (zip_name, open(zip_path, "rb"), "application/zip")}
data  = {"job_id": ..., "video_id": ..., "channel_title": ...,
         "video_url": ..., "clip_count": ...}
headers = {**settings.headers}   # arbitrary key/value list
if settings.secret:
    headers["X-OpenShorts-Signature"] = hmac_sha256(zip_bytes, settings.secret)
```

The HMAC signs the **ZIP file bytes**, not the multipart envelope (the envelope
contains random boundaries), so the receiver reads the file part and verifies
it. Timeout is long (default 600 s). Redirects are off. `assert_public_url`
still runs at submit and delivery; the operator exposes the target through a
tunnel.

### Retry + outbox

Delivery state lives in `outbox/<job_id>.json`, frozen with the target config
at completion time so a later settings change cannot alter an in-flight
delivery. A loop scans the outbox every 5 min (and on startup) and retries
pending entries with `attempts < 3`; on 2xx the ZIP and state are removed.
ZIPs live in the outbox, not the job dir, so the janitor cannot delete them
before they are delivered.

Manual re-send from History hits `POST /api/automation/deliveries/{job_id}/retry`,
which rebuilds the ZIP from the job's current canonical clips and resets the
outbox entry.

## API endpoints (self-host)

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/automation` | Settings + subscriptions + pending + outbox summary. |
| POST | `/api/automation/settings` | Save hour, timezone, delivery url/headers/secret. |
| POST | `/api/automation/channels` | Add channel (resolve id, register WebSub). |
| DELETE | `/api/automation/channels/{id}` | Remove channel + hub unsubscribe. |
| POST | `/api/automation/run-now` | Run the daily pass immediately. |
| POST | `/api/automation/deliveries/{job_id}/retry` | Re-send one ZIP. |
| GET/POST | `/api/automation/youtube/callback` | WebSub verification / notification. |

## Dashboard

A new **Automation** panel reachable from the profile menu / settings:

- Master toggle, run-hour picker, timezone (default Asia/Jakarta).
- Delivery: URL, key/value header rows (add/remove), secret, file field name.
- Channel list: add by URL/handle, show title + lease status, remove.
- Pending list: video, channel, published time, status, "Run now".
- Status of outbox entries; delivery failures with a **Re-send** button.
- History tab gains a **Re-send ZIP** action for automation jobs.

## Resilience ("never stop mid-job")

- Pipeline resume is the existing manifest mechanism; automation goes through it.
- Pending queue and schedule state are on disk, so PG/PM2 restarts lose nothing.
- Each video is an independent job: one failure never aborts the batch.
- Outbox survives restart and retries independently of the job process.
- RSS fallback covers dropped WebSub pushes.

## Config

| Var | Default | Meaning |
|---|---|---|
| `AUTOMATION_DIR` | `./automation` | State directory (gitignored). |
| `PUBLIC_API_URL` | — | Absolute base for the WebSub callback. Must be the tunnel URL. |
| `AUTOMATION_POLL_MINUTES` | 30 | RSS fallback interval. |
| `AUTOMATION_DELIVERY_TIMEOUT` | 600 | Seconds per delivery POST. |

Run hour, timezone and delivery target live in `automation/settings.json`,
edited from the dashboard.

## Out of scope (v1)

- Cloud mode / multi-user.
- Backfill of existing channel videos.
- Per-channel recipe overrides.
- Email/Telegram notifications (History is the surface).
- Social posting of the clips (Upload-Post) — delivery is the ZIP only.

## Phases

1. File store + settings API + dashboard settings card.
2. Channel add/resolve + WebSub subscribe/verify/callback + RSS fallback + pending list.
3. Scheduler daily pass + submit refactor + retry state machine.
4. ZIP helper + multipart delivery + outbox retry + History re-send.
5. Tests (`test_automation_store`, `test_channel_watch`,
   `test_automation_delivery`, `test_automation_api`) and docs.

## Risks

| Risk | Mitigation |
|---|---|
| WebSub delayed/dropped | RSS fallback poll every 30 min. |
| Fresh upload only 360p | Quality gate → `retry` with backoff, not failure. |
| Huge ZIP (long source) | Stream from disk, long timeout, one ZIP per video. |
| `process_endpoint` refactor regression | Extract the tail only; existing endpoint tests + new tests. |
| Automation dir deletion | Top-level dir, outside `output/`, gitignored; janitor untouched. |

## Implementation notes

Modules: `automation.py` (file store), `channel_watch.py` (resolve / WebSub /
RSS), `automation_delivery.py` (ZIP + multipart). `app.py` gained
`_apply_job_options` + `_finalize_job` (the shared tail of `/api/process`,
which the autopilot now submits through), the scheduling and delivery loops, and
the `/api/automation/*` routes. The dashboard got `AutomationPanel.jsx` inside
Settings (self-host only) and a **re-send** button on History rows that have a
delivery.

Defaults chosen at implementation time, all matching a manual submit with no
advanced options: auto-hook follows the deployment default (off unless
`AUTO_HOOK=1`), captions follow the deployment default (on), layouts follow
`AUTO_LAYOUT`/`layout_env`, and the active AutoAudit ad is inherited when one
is enabled. Run-now performs today's pass and marks the day done, so the
scheduled run will not fire again that day.

Tests: `tests/test_automation_store.py`, `tests/test_channel_watch.py`,
`tests/test_automation_delivery.py`, `tests/test_automation_api.py`.
