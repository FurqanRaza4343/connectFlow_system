# Media in outreach (images, PDFs, videos, links)

A workspace keeps a **media library** (Outreach > Assets). Its items can be attached to first messages, follow-ups and AI replies, always
through the same approval, quota, warm-up, STOP and active-hours rules as text. This page is the backend reference; the UI is described in
`docs/MEDIA_UI.md`.

## What an item is
| kind | accepted files | limit per file |
|---|---|---|
| `image` | JPEG, PNG, WebP (re-encoded to JPEG, max 1600 px, **all metadata removed**, 320 px thumbnail) | 5 MB |
| `document` | PDF (no JavaScript / embedded files / launch actions) | 10 MB |
| `video` | MP4 / MOV | 16 MB |
| `link` | an `https://` URL (public hosts only; the page title is fetched best-effort) | no file |

Files are validated by their **bytes** (magic numbers), never by name or declared type: executables, scripts, archives/Office files, SVG/HTML/XML,
GIF, a PDF pretending to be a PNG, and images above 40 megapixels are refused. Items carry a label and tags (`services`, `pricing`, `portfolio`,
`trust`, `booking`, ... free text, lowercase slugs, max 10). An item can have up to **5 variants** (extra files of the same kind); the sender rotates
through the original and its variants so the same bytes are not sent to hundreds of strangers.

## Plan limits
| plan | storage | items | uploads |
|---|---|---|---|
| Trial | 20 MB | 5 | 30 per hour per workspace (all plans) |
| Starter | 200 MB | 30 | |
| Growth | 2 GB | 200 | |

Limits are columns on `plans` (`asset_bytes_limit`, `asset_count_limit`) and are editable like the other plan figures. A limit hit answers
**402** `{detail, limit, used, plan, upgradeUrl}`; other refusals: 413 (too large), 415 (not allowed), 422 (does not match / unreadable), 429 (rate).
The check and the reservation happen under the tenant row lock, so parallel uploads can never overshoot (the last slot goes to exactly one of them).
Existing items are never deleted when a plan is downgraded; new uploads are blocked until usage fits again.

## Where the bytes live (`STORAGE_BACKEND`)
Postgres only holds metadata (`assets`, `asset_variants`, `draft_assets`, `messages.asset_ids`).

* **`local`** (default): Docker volume `assets-data` mounted at `/data/assets` in `api` and `worker`; one folder per workspace; file names are random
  ids, never user input. Back it up with the rest of `/var/lib/docker/volumes` (or copy the volume); the Postgres backup alone does not contain files.
* **`cloudinary`**: set `STORAGE_BACKEND=cloudinary` and `CLOUDINARY_URL=cloudinary://<api_key>:<api_secret>@<cloud_name>` in `infra/.env`
  (the URL is a **secret**: never commit it, never paste it into chat). Uploads are signed REST calls (no SDK), `type=private`, folder
  `connectflow/<tenant_id>/`; nothing is publicly fetchable: the API downloads through a signed private-download URL and streams the file to the
  signed-in member. `resource_type`: image / video / raw (PDF). Provider errors map to **507** (quota or rate limit at Cloudinary) and **502**
  (anything else); credentials never appear in responses or logs. Assets remember which backend stored them, so switching the setting later never
  strands old files.
* Cloudinary's free plan is measured in credits (storage + bandwidth + transformations) and is small: our own per-plan limits apply first, and a
  heavily used account will need a paid Cloudinary plan or the S3/R2 backend later (add a class behind `app/storage.py`, nothing else changes).

Housekeeping (worker, hourly, `app/assets_sweep.py`): upload reservations that never completed are removed after an hour; files on the local volume
that nothing refers to (failed uploads, deleted workspaces) are deleted after an hour; an admin deleting a customer also removes its stored files.

## How a draft carries media
* `outreach.media` (profile, `PUT /api/outreach/profile`):
  `{firstMessage:{mode:'off'|'image'|'link', assetIds}, followup1:{assetIds}, followup2:{assetIds}, replies:{enabled, allowTags}}`. Ids must be the
  workspace's own items; `image` mode needs files, `link` mode needs links; at most 5 candidates for the first message, 3 per follow-up.
* **First message = ONE WhatsApp message**: a file with the text as its caption (≤ 1024 characters), or the text with the link written into it and a
  WhatsApp link preview. Several candidates rotate (round-robin in Redis); the chosen file also rotates through its variants.
* **Follow-ups**: the configured items of that follow-up; the first file carries the text as its caption, further files follow as separate messages
  (3-10 s random pause between parts). Every part counts against the daily plan quota; if the quota ends mid-way the rest is dropped and the draft note says so.
* **AI replies**: the model may *propose* ids from the allow-list (items whose tags intersect `replies.allowTags`; an empty list allows nothing;
  `replies.enabled=false` disables it; a reply that needs a human gets none). The server re-validates every id (exists, same workspace, tag allowed,
  at most 3). The model never writes URLs of its own (prompt rule), and replies stay `pending` unless auto-reply is on.
* Operators can change a waiting draft: `PUT /api/drafts/{id}/attachments {assetIds}` (a first message takes at most one).
* Links are written **into the text** when the draft is created, so the approver sees exactly what will be sent.
* An item attached to a pending/approved/sending draft cannot be deleted (409); deleting an item also removes it from the outreach settings.

## Safe-sending rules (constants in `app/media.py`)
| rule | value |
|---|---|
| a number younger than | **3 days** never sends media (the message goes out as text only, with the reason on the draft) |
| while the number is warming up (< 5 days) | media is at most **30 %** of that day's outbound messages from the number (the 4th message may carry media, then the 7th, ...) |
| pause between parts of one draft | random 3-10 s |
| variants | up to 5, round-robin per send |
| everything else | same daily cap, warm-up cap, plan quota, active hours, idempotency, STOP handling as text |

`OUTREACH_FAKE_SEND=true` fakes media sends too (no gateway call, no storage read) and still records `messages.media_kind` / `asset_ids`.
Inbound images, PDFs, voice notes and stickers are no longer dropped: they are stored as `[image]`, `[voice]`, ... placeholders (no download:
`MEDIA_DOWNLOAD_ENABLED` stays false), the lead is marked replied, the owner gets a notification + e-mail, and no AI draft is created.

## Public API
`POST /v1/messages` accepts `media: {type: image|document|video, assetId | url, caption?, filename?}` (exactly one of `assetId` / `url`). A `url`
must be public https; we download it ourselves (no redirects, size-capped, content validated against the declared type) and send the bytes, so the
gateway never fetches a URL on a customer's behalf. Same quota, number-state and idempotency rules as text.

## Database and system pressure (what changed and why it holds)
* **Bytes never go to Postgres.** A media message is one extra small row, like a text message.
* **New composite indexes** (migration 0011): `messages(tenant_id, direction, kind, created_at)`, `usage_events(tenant_id, kind, at)`,
  `leads(tenant_id, status, followup_stage, sent_at)`. They are created with plain `CREATE INDEX IF NOT EXISTS` (tables are small today). Before
  upgrading a database with millions of rows, create them by hand with `CREATE INDEX CONCURRENTLY ...` first; the migration then does nothing.
* **Per-send COUNT queries replaced by Redis daily counters** (`app/counters.py`): `q:msg:<tenant>:<UTC day>` (plan quota),
  `og:first|out|media:<tenant>:<local day>` (per number). The database stays the audit source: a missing key is rebuilt from it once, an increment on a
  missing key is skipped (the rebuild already includes the row), refunds decrement, and if Redis is down the database answers directly.
* **Follow-ups**: one bounded query (`NOT EXISTS` for replies / open drafts, `LIMIT 3 x batch`) instead of loading every contacted lead and running
  two queries per lead.
* **Isolation of slow work**: each workspace sends in its own task and DB session (at most 8 in flight per worker); media uploads additionally
  pass a semaphore of 3, so one slow upload never delays another workspace's text. A transient gateway failure is retried once, then the draft fails
  with a plain reason; a failure after the first part of a multi-part draft keeps the draft `sent` with a note.
* **One pooled HTTP client** for the gateway (keep-alive) instead of a new client per request.
* **Limits at the edge**: Caddy accepts 25 MB only on `/api/assets*`, 6 MB on other `/api/*`, 1 MB on `/v1/*`.
* **Gateway memory**: a media send holds roughly 3x the file in memory (file, base64, JSON); the cap is 18 MB raw per send.

## Operating it
* `GET /api/admin/capacity` -> `storage: {backend, assetsTotal, bytesTotal, mediaSends1h, senderQueueDepth, uploadFailures1h}` (counts only).
* Disk use of the local backend: `docker compose exec api du -sh /data/assets`. If it fills up, uploads answer 507.
* Switching to Cloudinary: add the two lines to `infra/.env`, `docker compose up -d api worker`; old local files keep working.
* Tests: `python backend/tests/run_e2e.py media` (needs `AI_FAKE` + `OUTREACH_FAKE_SEND` on the stack) and `media_cloudinary` (needs the api started
  against the fake Cloudinary the script runs on `E2E_FAKE_CLOUDINARY_PORT`; it skips itself otherwise). Unit tests: `tests/test_media_units.py`.
