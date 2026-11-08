# ConnectFlow: system design, capacity and cost

Audience: engineers and mentors reviewing the design. Facts below were read from the code (file references given). Anything marked **estimate** is a modelling assumption that must be confirmed with a load test before it is promised to customers.

## 1. What the product is
A multi-tenant SaaS. Each customer (tenant) links their own WhatsApp number(s) by scanning a QR code and gets an isolated workspace with:
- a **WhatsApp API** (API keys, signed webhooks),
- an **AI lead-generation engine** (import leads, research, draft, human approval, paced sending with number warm-up, reply handling, follow-ups, optional media),
- a customer dashboard and a separate platform-admin dashboard, plus a marketing site.

Isolation: every row carries `tenant_id`; another tenant's row answers 404. Roles: owner, admin, operator, viewer. "Platform admin" is a separate flag, served only on the admin host.

## 2. Architecture
```
Browser
  |  HTTPS (Caddy: automatic TLS, one entrypoint, per-host routing)
  +-- website (Astro, static)         +-- customer app (React)   +-- admin app (React)
                                           |  /api
                                      FastAPI (uvicorn, 1 process)  -- Postgres 16 (all tenants, one DB)
                                           |                         -- Redis 7 (locks, counters, pacing, rate limits)
  Worker (1 process, 7 loops)  ---------+
     sender, scheduler, enrichment, webhooks, reaper, retention, billing/assets
        |-> wa-gateway (OpenWA + Baileys)  -> WhatsApp      [1 session = 1 number]
        |-> LLM (DeepSeek, OpenAI-compatible), SearXNG (search), Resend (email)
        |-> Stripe (billing), media storage (local volume or Cloudinary)
```
Only the API talks to the gateway (`backend/app/wa_client.py`). Customers never reach it.

### Technology inventory
FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, Redis 7, Caddy 2, React + Vite + TypeScript, Astro 4 (three.js, GSAP), OpenWA gateway running the **Baileys** engine, DeepSeek via an OpenAI-compatible API, SearXNG, Resend, Stripe (Checkout, portal, webhook), Cloudinary or a Docker volume for media, Sentry (optional), Docker Compose, GitHub Actions (ruff, mypy, pytest, migrations, builds, compose e2e, secret scan).

### The "queue"
There is no queue library. Sending is a **database state machine** (`drafts.status`: pending, approved, sending, sent or failed) claimed with `SELECT ... FOR UPDATE SKIP LOCKED`; the sender loop polls every 2 s. Redis holds the per-tenant pacing gap (`og:gap:{tenant}`, 60-180 s random), daily counters (self-heal from the DB), idempotency keys, rate limits and heartbeats. Advantage: queue state is transactional with business data and survives Redis loss. Webhook deliveries use the same pattern with 6 retries (30 s, 2 m, 10 m, 1 h, 6 h).

### Safety by design (the product's edge)
Per-number warm-up (5, 10, 15, 20, 30, 40 first messages a day by number age), daily caps, active hours, approval before every send (default), strict STOP handling, plan quotas, per-tenant AI caps, paced random gaps, no media on numbers younger than 3 days and at most 30% media while warming up, capacity guard with waitlist, session reaper, data-retention jobs, encrypted backups, API-key telemetry with auto-suspend, 2FA.

## 3. Components: what each gives and costs
| Component | Benefit | Limit / risk |
|---|---|---|
| FastAPI, JWT cookie (stateless) | easy to replicate behind Caddy | single uvicorn process today |
| Postgres (shared schema) | simple, transactional, cheap | untuned defaults; `messages` and `leads` grow without retention |
| Redis | fast locks and counters | no persistence configured today (being fixed) |
| Baileys via OpenWA | no browser, low RAM | unofficial protocol: ban risk, breaks when WhatsApp changes |
| One gateway node | simple | **single point of failure and the hard capacity limit** |
| LLM | the product's value | the largest variable cost |
| Cloudinary | no local disk use | bandwidth credits grow with every media send |

## 4. Where Chromium is (and is not)
Chromium exists **only in the legacy single-tenant OpenWA service** (`openwa`, engine whatsapp-web.js, `PUPPETEER_*` settings in `infra/docker-compose.yml`). Measured: 11 Chromium processes and about 900 MB RAM there, versus 0 processes and about 130 MB in the new `wa-gateway` (`ENGINE_TYPE=baileys`). Both use the same image; the engine is a deployment setting. Chromium runs inside the container, so it never appears in a browser's DevTools; verify with `docker exec <container> ps aux | grep chrom`, `docker stats`, the container's `ENGINE_TYPE`, or OpenWA's Infrastructure page. It was not required: Baileys speaks the WhatsApp protocol directly.

## 5. Capacity model
Terms: a **node** is one gateway server (container) hosting many WhatsApp sessions. Capacity is counted in **numbers (sessions), not users**. The cap is `GATEWAY_MAX_SESSIONS` (default 20); `PLATFORM_MAX_INSTANCES` must not exceed it. Plan limits per tenant: Trial 1 number, Starter 3, Growth 10. When the cap is reached, the system does not degrade: new numbers get HTTP 503 and the tenant joins a waitlist (`instances.py`), existing sessions keep working. The business consequence is that two Growth customers using 10 numbers each fill a 20-session node and block onboarding of the next customer.

Sizing from OpenWA's own table (`docs/RUNBOOK.md`): 1-5 sessions 1 GB, 5-10 2 GB, 10-25 4 GB, 25-50 8 GB. No measured per-session RAM exists yet (idle gateway about 130 MB).

### Workload assumptions (estimate)
One "user" = a paying workspace with 1.5 numbers on average, about 100 new leads a day, about 300 outgoing messages a day (first messages plus follow-ups), about 15% replies, and the Starter AI cap of 200 calls a day.

| | 5 users | 50 users | 100 users | 1000 users |
|---|---|---|---|---|
| WhatsApp sessions | about 8 | about 75 | about 150 | about 1,500 |
| Gateway nodes (8 GB, about 50 sessions) | 1 | 2 (or one 16 GB) | 3 | 25-30 |
| API replicas | 1 | 1-2 | 2-3 | 4-8 |
| Worker replicas | 1 | 1 | 2 | 4-6 |
| Postgres | 1 | 1, tuned | 1, tuned (+PgBouncer when replicas grow) | managed + read replica |
| Needs code that is not built yet | none | gateway node registry | registry, worker fixes | orchestration, partitioning |

### Database load and growth (estimate)
- 100 users: about 30k messages a day, about 2-3 writes per second. 1000 users: about 25 writes per second. Postgres handles this easily; storage growth is the issue.
- Row sizes (from the models): lead 3-6 KB with research, message about 1 KB, draft about 1 KB, usage event about 100 B. About 0.5 GB per active user per year including indexes: **100 users about 50 GB a year, 1000 users about 0.5 TB a year**. `messages` and `leads` have no retention today; plan monthly partitioning of `messages` and cold archival.
- Connections: pools are 10 + 10 per process (`db.py`). API + worker use at most 40 of Postgres's default 100. PgBouncer (transaction mode, prepared statements disabled) becomes useful once there are 3 or more API/worker replicas.
- The gateway keeps its auth/session state in a second database (`openwa`) on the same server. At scale that should become its own Postgres.
- Media bytes are never stored in Postgres (metadata only).

### Throughput
Sending is paced per tenant (60-180 s gap), so average send rate is tiny: 100 users is roughly 0.35 messages per second. The sender handles 8 tenants concurrently per worker; at 1000 simultaneously active tenants a single worker would lag, so use replicas or raise `SEND_CONCURRENCY` after measuring.

## 6. Known gaps (found in review)
1. One gateway node, cap 20 sessions; no node registry (`instances.node_id` is always `wa1`).
2. Worker is not safe to run as two replicas yet: `recover_interrupted()` fails all in-flight drafts on start; scheduler, digest, billing and assets loops have no distributed lock; two API replicas both run migrations at start.
3. Admin capacity page read the wrong Redis keys for AI counters.
4. LLM retries (up to 4) are free of quota; the global AI breaker is off unless configured; no token or dollar accounting.
5. Postgres and Redis run on image defaults (no tuning, no Redis persistence, no memory limits).
6. Media is re-read from storage for every send (no cache).
7. No retention for `messages` and `leads`; indexes created non-concurrently.
Items 2-6 are the P0 work in `docs/RUNBOOK.md` ("Scaling and operations"); items 1 and 7 are P1.

## 7. Cost model
### LLM (largest variable cost)
Per new lead: research + first draft = 2 gated calls, about 4.5k input and 280 output tokens (**estimate**, about 4 characters per token). Follow-ups add about 2 calls, replies 1 each. Per-plan call caps: Trial 20, Starter 200, Growth 1000 a day (about 100 new leads a day at the Starter cap).

Provider price per million tokens is the biggest uncertainty (published figures disagree; always read the provider's price page). Examples at the Starter cap (200 calls, about 0.6M input and 24k output tokens a day):
| Price assumption (USD per 1M input / output) | Cost per message | Per Starter tenant per month |
|---|---|---|
| 0.14 / 0.28 (Flash-class) | about $0.0007 | about $3 |
| 0.435 / 0.87 (Pro, low slab) | about $0.002 | about $8 |
| 1.74 / 3.48 (Pro, high slab) | about $0.009 | about $34 (more than the $19 Starter price) |
Model choice therefore decides margin. Levers: cheaper model for drafts and extraction (`AI_MODEL_FAST`), prompt caching of the static system prompt, a cap on knowledge text injected per call (`AI_KNOWLEDGE_MAX_CHARS`), per-plan call caps, the global breaker.

### Media
Each media message reads the file and posts it to the gateway base64-encoded (+33%). 100 users x 300 messages x 1 MB is about 30 GB a day (**estimate**). Cloudinary's free plan has 25 credits and one credit equals 1 GB of bandwidth (cloudinary.com/pricing), so at volume use small files, a byte cache in the worker, or S3-compatible storage (R2/B2).

### Infrastructure (estimate; verify live prices, cloud prices changed in 2026)
| Users | Infrastructure per month | Notes |
|---|---|---|
| 5 | $20-40 | one 4 vCPU / 8 GB VPS runs everything |
| 50 | $100-150 | app + DB box, plus a 16 GB gateway box (or two 8 GB) |
| 100 | $250-400 | 8 vCPU / 16 GB app + DB, 3 gateway nodes, object storage |
| 1000 | quote after load test | 25-30 gateway nodes, managed Postgres, replicas |
Other costs: domain, Resend (email volume is low: verification, resets, alerts, digests), Stripe fees (payment percentage only), Sentry (optional), backups, monitoring, and support time.

## 8. Tooling questions
- **Is PgBouncer usable?** Yes. Not needed until about 3 replicas; then run it in transaction mode and disable prepared statements in the Postgres driver. Advisory locks used here are transaction-scoped, which is compatible.
- **Is BullMQ used?** No. The backend is Python and the legacy Node app has no dependencies. A queue library would help with delayed jobs, retries and visibility; Python options are arq, Dramatiq, Celery or the Postgres-based Procrastinate. The present DB queue is durable and transactional; Redis here is not persistent, so a Redis queue would lose jobs on restart unless AOF is enabled. Keep the DB queue until job volume or observability demands more.
- **One image per instance?** No. "Instance" = a WhatsApp session inside the gateway process. API and worker share one image; scale by adding replicas of the same images.
- **Is Baileys required?** It is the engine the gateway uses; an official WhatsApp Cloud API tier (Meta) is a separate, safer product line for opted-in audiences, with templates and per-conversation fees, and it does not allow cold outreach.

## 9. Deployment path
Single VPS first (`infra/docker-compose.prod.yml` adds public 80/443, certificate volumes and log rotation), then split the gateway onto its own server(s) once the node registry exists, then managed Postgres. Secrets live only in `infra/.env` on the server; see `docs/SECRETS.md`, `docs/RUNBOOK.md`.

## 10. Before promising numbers: load test
100 tenants x 300 messages a day against a fake gateway, 20 tenants with media; record API p95 latency, send lag per tenant, DB CPU and connections, Redis memory, per-session gateway RAM, LLM tokens and cost per tenant per day. The plan's verification section lists the acceptance criteria.
