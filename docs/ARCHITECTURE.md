# Architecture

```
Browser ──> caddy ─┬─ connectflow.localhost      -> static marketing website
                   ├─ admin.connectflow.localhost -> separate Platform Admin SPA (admin.html) + /api
                   └─ app.connectflow.localhost  -> static React app, /api/* -> api
api (FastAPI) ──> postgres (tenants, users, instances, audit)
              ──> redis    (rate limits; queues in M2/M3)
              ──> wa-gateway (OpenWA, Baileys engine) : sessions = customers' WhatsApp numbers
wa-gateway ──webhook (HMAC)──> api /internal/wa-webhook   (private Docker network only)
```

## Rules
- Customers never see OpenWA or its admin key. `backend/app/wa_client.py` is the only gateway client.
- Every tenant-owned query goes through a helper that filters by `tenant_id` (e.g. `_own()` in `routers/instances.py`); another tenant's row looks like "not found" (404).
- Auth: argon2 password, JWT in an HttpOnly SameSite=Strict cookie, `X-Requested-With: ConnectFlow` required on state-changing calls (CSRF), Redis-based login throttling per IP and per account.
- `/internal/*` is not routed by Caddy.

## Containers
Core: `caddy`, `api`, `worker` (webhook delivery), `postgres`, `redis`, `wa-gateway`, `mailpit` (dev mail catcher, remove in production). Legacy profile adds `openwa`, `orchestrator`, `searxng` until milestone M4 (SearXNG then returns as a core service for lead enrichment).

## Milestones
M0 restructure (done) · M1 marketing website (done, awaiting owner review of design) · M2 platform completion (**done:** Alembic, API keys, public `/v1` send API, usage + daily quota, customer webhooks (signed, retried, delivery log, SSRF-guarded), email verification + password reset + team invites (Mailpit locally), manual billing/upgrade requests, workspace branding; **todo:** Stripe checkout, 2FA, real SMTP in production) · M3 lead-gen module (**done, see docs/M3_CONTRACT.md:** AI research/drafting, approval queue, paced sender with warm-up, scheduler, replies/STOP, follow-ups, notifications, Overview/Approvals/Inbox UI, legacy migration tool `python -m app.tools.migrate_legacy`; earlier slice:** leads, lists, CSV import with column mapping + phone normalisation (`phonenumbers`), dedupe, export, per-workspace outreach profile; **todo:** enrichment, AI research/drafting, approval queue, paced sender + warm-up, reply handling, follow-ups) · M4 admin completion + One Group migration · M5 production readiness.

## Public API (M2)
`Authorization: Bearer cf_live_...` on `/v1/*` (keys are hashed at rest, scoped to all numbers or one, revocable, 120 req/min).
`GET /v1/instances`, `POST /v1/messages {instance, to, text}` -> 202 sent · 401 bad key · 403 wrong scope · 409 number not connected · 422 invalid / not on WhatsApp · 429 daily or rate limit.
The dashboard and the API share one send path (`backend/app/messaging.py`) and one limit (`backend/app/quota.py`, row-locked so parallel requests cannot exceed the plan).

## Migrations
`backend/alembic/`. The API runs `alembic upgrade head` at startup (a database that predates Alembic is stamped at `0001` first). Add a migration: `alembic revision --autogenerate -m name` against a scratch database.

## Webhooks (customer-facing)
Events `message.received`, `instance.status` (+ `ping` test). POST JSON with `X-ConnectFlow-Event`, `-Delivery`, `-Timestamp`, `-Signature: sha256=HMAC(secret, timestamp + "." + body)`.
Retries: 30 s, 2 min, 10 min, 1 h, 6 h (6 attempts), then `failed`; manual retry in the dashboard. URLs must be public https (`WEBHOOK_ALLOW_PRIVATE=true` only for local development).

## Branding
`tenants.brand_color` + `tenants.logo_data` (PNG/JPEG/WebP/SVG, <= 256 KB, content-sniffed, SVG scripts rejected). The React app applies the colour as CSS variables (`frontend/src/theme.ts`).

## Hosts (local)
`connectflow.localhost:8080` website · `app.connectflow.localhost:8080` customer app · `admin.connectflow.localhost:8080` platform admin (separate bundle, own cookie jar, platform-admin accounts only). Anything else (e.g. `127.0.0.1:8080`) redirects to the website.
The admin API (`backend/app/routers/admin.py`, `admin_ext.py`) exposes counts, emails, logins, activity trail, numbers, keys (prefix only), webhooks and plans, but never message content.

## M3 — lead-gen engine (backend)
Contract: `docs/M3_CONTRACT.md`. Code: `backend/app/{ai,enrich,outreach,outreach_worker}.py`, routers `drafts.py`, `outreach.py` (status, run-now, batches, overview, notifications, inbox) and the lead endpoints in `leads.py`.
- **Flow:** leads (status `new` + valid number) -> batch (scheduler or "Run now") -> research (SearXNG + site crawl + AI) -> AI draft (profile: company, sender, offer, knowledge, tone) -> approval queue -> paced sender -> `contacted` -> inbound webhook -> STOP / AI classified reply draft (needs-human for pricing/complaints) -> follow-ups (day 3 / day 7).
- **Safety:** dry-run default (drafts visible, nothing sends), approval default, tenant daily cap, per-number warm-up `[5,10,15,20,30,40]`, human-like random gap, plan quota, idempotency (never a 2nd first message), `sending` drafts become `failed` after a crash, strict STOP (<= 60 chars).
- **Worker (`worker` service):** webhook delivery + scheduler (30 s) + sender (2 s, Redis lock/gap per workspace) + enrichment queue (Redis). Safe with several workers (`FOR UPDATE SKIP LOCKED`, Redis locks).
- **SearXNG** is now a core service (lead research); crawling uses `netguard.assert_crawlable` (public hosts only, redirects re-validated).
- **AI:** OpenAI-compatible (`AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL`); all lead/web/inbound text is wrapped in delimiter tags and declared untrusted in the system prompt. `AI_FAKE=true` / `OUTREACH_FAKE_SEND=true` are test-only switches.
