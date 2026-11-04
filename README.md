# ConnectFlow

**Multi-tenant WhatsApp platform + AI lead-generation outreach.**
Every customer signs up, links their own WhatsApp number(s) by scanning a QR code, and gets a private workspace with:

- a **WhatsApp API**: send/receive messages, API keys, signed webhooks;
- an **AI outreach engine**: import leads, AI research and drafting, human approval, paced sending with number warm-up, reply handling and follow-ups;
- a **customer dashboard** (branding, team, plans) and a separate **platform-admin dashboard**;
- a **marketing website**.

> Status: M0-M4 are built and tested locally. Billing is a manual upgrade-request flow (Stripe is not built yet). See [Roadmap](#roadmap).

---

## Contents
1. [Architecture](#architecture)
2. [Repo map](#repo-map)
3. [Requirements](#requirements)
4. [Quick start (Docker)](#quick-start-docker)
5. [Docker commands cheat sheet](#docker-commands-cheat-sheet)
6. [Configuration](#configuration)
7. [Develop and test](#develop-and-test)
8. [Operate](#operate)
9. [Security model](#security-model)
10. [Production deploy](#production-deploy)
11. [Docs](#docs)
12. [Roadmap](#roadmap)

---

## Architecture

```
                         Caddy (single entrypoint, :8080 locally / :443 in production)
        connectflow.*  ->  marketing website (Astro, static)
        app.*          ->  customer app (React)      + /api -> FastAPI
        admin.*        ->  platform admin (React)    + /api -> FastAPI
                                   |
   +-------------------------------+---------------------------------+
   |                               |                                 |
 api (FastAPI)  <--- Redis --->  worker (sender, scheduler,        wa-gateway
 auth, tenants, keys,            webhook delivery, retention,      (OpenWA, Baileys engine)
 leads, drafts, /v1 API          reaper, digests)                  one WhatsApp session per number
   |                               |                                 |
   +----------- Postgres 16 -------+-------- (separate DB "openwa") --+
```

- **Tenancy:** every row carries `tenant_id`; another tenant's row is a 404. Roles: owner, admin, operator, viewer. "Platform admin" is a separate flag.
- **Only the API talks to the gateway** (`backend/app/wa_client.py`). Customers never reach it.
- **Hosts:** the three apps run on separate hostnames because browser cookies are per host, not per port.

## Repo map

| Folder | What it is |
|---|---|
| `backend/` | FastAPI API + worker (Python 3.12, SQLAlchemy 2, Alembic). Tests in `backend/tests/` |
| `frontend/` | React + Vite + TypeScript: customer app (`index.html`) and admin app (`admin.html`) |
| `website/` | Marketing site (Astro 4, three.js, GSAP) |
| `gateway/` | Notes for the WhatsApp gateway (OpenWA / Baileys) |
| `infra/` | `docker-compose.yml`, `Caddyfile`, `.env.example`, backup/restore and security scripts |
| `docs/` | Architecture, runbook, outreach contract, secrets and GitHub guides |
| `.github/` | CI (lint, tests, migrations, builds, full-stack e2e) and secret scanning |

Not in Git by design: real `.env` files, data volumes, backups, the old single-tenant `legacy/` archive, `vendor/`. See [docs/GITHUB.md](docs/GITHUB.md).

## Requirements

- Docker Desktop (or Docker Engine) with **Compose v2** (`docker compose version`)
- ~6 GB free RAM for the full stack, ~5 GB disk for images
- Optional for local development without Docker: Python 3.12, Node 20+

## Quick start (Docker)

```bash
git clone https://github.com/FurqanRaza4343/connectFlow_system.git
cd connectFlow_system/infra

# 1. create your private config (this file is git-ignored)
cp .env.example .env

# 2. generate strong secrets and paste them into .env
#    (POSTGRES_PASSWORD, JWT_SECRET, OPENWA_WEBHOOK_SECRET, OPENWA_DB_PASSWORD, ADMIN_PASSWORD)
openssl rand -hex 32          # run once per secret   (PowerShell: [Convert]::ToHexString((1..32|%{Get-Random -Max 256})) )

# 3. build all images and start the stack
docker compose up -d --build

# 4. check that everything is healthy
docker compose ps
```

Open (local defaults, port 8080):

| What | URL |
|---|---|
| Website | http://connectflow.localhost:8080 |
| Customer app | http://app.connectflow.localhost:8080 |
| Platform admin | http://admin.connectflow.localhost:8080 (login = `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `.env`) |
| Dev mail inbox (Mailpit) | http://localhost:8025 |

Use the `*.localhost` names, not `localhost:8080`. A shared `localhost` can collect so many cookies that Caddy answers **400 Request Header Or Cookie Too Large**; if you ever see it, clear cookies for `localhost`.

Database migrations run automatically when the API starts. The first platform admin is created from `ADMIN_EMAIL` / `ADMIN_PASSWORD` if it does not exist.

**First run walk-through:** sign up on the app host -> verify the email in Mailpit -> *WhatsApp numbers* -> *Add number* -> scan the QR with the phone -> import leads -> *Outreach > Setup* -> approve drafts. Outreach starts in **dry-run** with **approval required**, so nothing is sent until you turn that off.

## Docker commands cheat sheet

Run from `infra/` unless noted.

```bash
# --- build ---
docker compose build                       # build every image (api, worker, caddy+website+UIs)
docker compose build --no-cache api        # rebuild one image from scratch
docker compose build api worker caddy      # build selected services only

# --- run ---
docker compose up -d --build               # build + start everything in the background
docker compose up -d --build api worker    # rebuild and restart only the backend after a code change
docker compose --profile monitoring up -d  # + Uptime Kuma
docker compose ps                          # status and health
docker compose logs -f api worker          # follow logs (JSON)

# --- stop ---
docker compose stop                        # stop, keep containers and data
docker compose down                        # remove containers, KEEP volumes (your data)
# docker compose down -v                   # DANGER: also deletes volumes = database and WhatsApp sessions

# --- single images without compose ---
docker build -t connectflow-api ../backend                 # backend image (API and worker share it)
docker build -f caddy.Dockerfile -t connectflow-web ..     # website + both React apps + Caddy

# --- one-off commands inside the stack ---
docker compose exec postgres psql -U connectflow connectflow
```

Or use the Makefile from the repo root:

```bash
make up            # build + start (never removes volumes)
make ps            # container status and health
make logs          # follow api + worker logs
make lint          # ruff
make test          # backend unit tests
make build-ui      # typecheck + build React apps and the website
make e2e           # end-to-end suites against the running stack
make backup        # encrypted backup set in ./backups
make restore-drill # restore the newest backup into a scratch DB and compare row counts
```

## Configuration

All configuration is environment variables in `infra/.env` (template: [`infra/.env.example`](infra/.env.example)). Key groups:

| Group | Variables |
|---|---|
| Core secrets | `POSTGRES_PASSWORD`, `JWT_SECRET`, `OPENWA_WEBHOOK_SECRET`, `OPENWA_DB_PASSWORD`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` |
| Hosts | `SCHEME`, `SITE_HOST`, `APP_HOST`, `ADMIN_HOST`, `APP_PORT`, `PUBLIC_APP_URL`, `COOKIE_SECURE` |
| Email | `RESEND_API_KEY` (production) or `SMTP_*` (Mailpit locally), `SMTP_FROM` |
| AI | `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL` (any OpenAI-compatible API, DeepSeek by default), `SEARXNG_URL` |
| Google sign-in | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (leave empty to hide the button) |
| Gateway limits | `GATEWAY_MAX_SESSIONS`, `GATEWAY_MEM_LIMIT`, `PLATFORM_MAX_INSTANCES` |
| Housekeeping | `REAPER_*`, `RETENTION_*`, `BACKUP_PASSPHRASE`, `SENTRY_DSN`, `LOG_JSON` |
| Dev/test switches | `AI_FAKE`, `OUTREACH_FAKE_SEND`, `WEBHOOK_ALLOW_PRIVATE` - **must be off in production** |

With `COOKIE_SECURE=true` the API refuses to start if a secret is weak (under 32 characters or a placeholder) or if a dev/test switch is on.

## Develop and test

```bash
make test        # pytest, no database or network needed
make lint        # ruff
make build-ui    # tsc + vite build, astro build
make e2e         # end-to-end suites; admin suites need E2E_ADMIN_PASSWORD in the environment
make e2e-m4      # one suite: m2, m3, flow_m3, m3_engine, m4, security, multinumber
```

- `m3_engine` and `multinumber` need `AI_FAKE=true` and `OUTREACH_FAKE_SEND=true` on api/worker; `m2` reads Mailpit and skips itself when `RESEND_API_KEY` is set. Skipped suites exit 77 and show as SKIP.
- CI ([.github/workflows](.github/workflows)): ruff, pytest, migrations up/down/up on a scratch DB, `tsc` + `vite build`, `astro build`, then the full compose stack with fake AI/send and Mailpit, plus a secret scan.
- Frontend only: `cd frontend && npm install && npm run dev`. Website: `cd website && npm install && npm run dev`.

Developer API (per-workspace API key, `Authorization: Bearer cf_live_...`): `POST /v1/messages`, `GET /v1/messages`, webhook events `message.received`, `instance.status`, `ping`, signed with `X-ConnectFlow-Signature: sha256=HMAC(secret, timestamp + "." + body)`. The website `/docs` page has the full reference.

## Operate

See [docs/RUNBOOK.md](docs/RUNBOOK.md) for capacity and sizing, the session reaper, data retention, monitoring and the restore drill.

```bash
make backup                                  # needs BACKUP_PASSPHRASE
make restore-drill                           # safe: restores into a scratch DB
curl http://127.0.0.1:8080/api/health/ready  # DB + Redis + gateway readiness (with Host header of the app)
```

Platform admins also get `GET /api/admin/capacity` (gateway sessions, DB size, retention, backups, AI usage).

## Security model

- Passwords: argon2. Sessions: JWT in an HttpOnly, SameSite=Strict cookie. State-changing calls need `X-Requested-With: ConnectFlow` and a same-origin `Origin`.
- API keys: 256-bit random, stored only as a SHA-256 hash; shown once; expiry, scope, IP allow-list, revoke/delete; usage telemetry with automatic suspension on suspicious use.
- Webhooks: public https targets only (SSRF guard), HMAC-signed, retried with back-off.
- Tenant isolation on every query; admins cannot read message content.
- Secrets never live in Git: [docs/SECRETS.md](docs/SECRETS.md) (inventory and rotation), [SECURITY.md](SECURITY.md) (reporting). A pre-commit hook and CI run `infra/scripts/check-secrets.py`.

## Production deploy

1. A server with Docker and a domain whose DNS points at it (`example.com`, `app.example.com`, `admin.example.com`).
2. In `infra/.env` set `SCHEME=https`, `SITE_HOST`, `APP_HOST`, `ADMIN_HOST`, `PUBLIC_APP_URL`, `COOKIE_SECURE=true`, strong secrets, `RESEND_API_KEY`, `AI_API_KEY`; keep `AI_FAKE`, `OUTREACH_FAKE_SEND` and `WEBHOOK_ALLOW_PRIVATE` off. Caddy then obtains TLS certificates automatically.
3. `cd infra && docker compose up -d --build`
4. Verify the sending domain in Resend, enable `make backup` on a schedule, and add the monitoring profile.

Sizing guide: a 4 vCPU / 8 GB / 80 GB NVMe box runs everything with about 20 WhatsApp numbers; beyond that, move Postgres off-box and add gateway nodes.

## Docs

| File | Topic |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design |
| [docs/M3_CONTRACT.md](docs/M3_CONTRACT.md) | Outreach engine rules (drafts, approvals, warm-up, STOP handling) |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Operations |
| [docs/SECRETS.md](docs/SECRETS.md) | Secret inventory and rotation |
| [docs/GITHUB.md](docs/GITHUB.md) | What is and is not in Git |
| [docs/CUTOVER.md](docs/CUTOVER.md), [docs/M4_PARITY.md](docs/M4_PARITY.md), [docs/LEGACY.md](docs/LEGACY.md) | Moving off the old single-tenant stack |

## Roadmap

Next (M5): domain + HTTPS and Resend domain verification, admin 2FA, signup abuse controls, idempotency keys on `POST /v1/messages`, encrypted webhook secrets, data export, Stripe checkout, a gateway node registry, full type hints with `mypy`. Later: official WhatsApp Cloud API provider, SSO, team inbox, autoscaling.

## Notice

Automated bulk messaging can breach WhatsApp's terms and local laws. Use opted-in contacts, keep the STOP handling on, and respect the daily caps and warm-up. This repository has no open-source licence: all rights reserved unless the owner adds one.
