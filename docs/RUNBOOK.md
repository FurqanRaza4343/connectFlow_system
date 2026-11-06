# ConnectFlow runbook

Operator guide: capacity, housekeeping, backups, restore drill, monitoring. Commands run from `infra/` unless stated.
Never run `docker compose down -v` or delete a volume on a live system: `pg-data` (all customer data) and `openwa-data` (WhatsApp sessions) live there.

## 1. Services at a glance
| Service | Role | Health |
|---|---|---|
| `caddy` | TLS/hosts, serves the SPA + website, proxies `/api` | docker healthcheck (admin endpoint) |
| `api` | FastAPI | `GET /api/health` (liveness), `GET /api/health/ready` (db + redis + gateway, 503 if any is down) |
| `worker` | webhook delivery, outreach engine, **session reaper/reconciler**, **retention**, heartbeat | Redis key `ops:worker:heartbeat` (< 60 s old) |
| `wa-gateway` | OpenWA / Baileys sessions; data DB = database `openwa` on the shared Postgres | `/api/health/ready` on the gateway |
| `gateway-db-init` | one-shot, idempotent: creates role/db `openwa` before the gateway starts | exits 0 |
| `postgres`, `redis`, `searxng` | data, queues/state, search | |
| `mailpit` (profile `dev`) | dev-only mail catcher, started with `--profile dev` / `make up-dev`; not used when `RESEND_API_KEY` is set | |
| `uptime-kuma` | optional (`--profile monitoring`) | http://127.0.0.1:3001 |

Logs are one JSON object per line (`ts, level, logger, msg, request_id, tenant_id, user_id, method, path, status, ms`). No bodies, headers, cookies or query strings are ever logged. Every response carries `x-request-id`; quote it when debugging.

## 2. Sizing and capacity (gateway)
OpenWA's own guidance, which `GATEWAY_MAX_SESSIONS` / `GATEWAY_MEM_LIMIT` follow:

| Concurrent sessions | RAM | CPU | `GATEWAY_MEM_LIMIT` |
|---|---|---|---|
| 1-5 | 1 GB | 0.5 vCPU | `1g` |
| 5-10 | 2 GB | 1 vCPU | `2g` |
| 10-25 | 4 GB | 2 vCPU | `4g` (default) |
| 25-50 | 8 GB | 4 vCPU | `8g` |
| above about 20-25 | run a **second gateway node** (the schema already has `instances.node_id`) | | |

* `GATEWAY_MAX_SESSIONS` (default 20) is a hard cap inside the gateway (`MAX_CONCURRENT_SESSIONS`).
* `PLATFORM_MAX_INSTANCES` (default 20, effective value = min of the two) is checked by the API under a Postgres advisory lock, so two simultaneous signups cannot both take the last slot. When full, `POST /api/instances` answers **503** with a friendly message and the tenant is appended (once) to the Redis list `waitlist:instances`. Read it: `docker compose exec redis redis-cli lrange waitlist:instances 0 -1`. After adding capacity, email those people and clear it: `redis-cli del waitlist:instances waitlist:instances:tenants`.
* Inbound media download is off (`MEDIA_DOWNLOAD_ENABLED=false`): the product never uses it, so no disk growth or memory spikes. Container hardening (read-only root, dropped capabilities) is unchanged.
* Gateway database: **PostgreSQL** (`DATABASE_TYPE=postgres`, `DATABASE_SYNCHRONIZE=false`; the gateway applies its own migrations). Reason: OpenWA's SQLite has no WAL/write queue and a single writer, which stalls under many sessions. Only the tiny auth DB (admin key) stays SQLite on the volume. If you ever have to go back to SQLite: set `DATABASE_TYPE=sqlite`, `DATABASE_SYNCHRONIZE=true`, and expect `SQLITE_BUSY` errors beyond about 10 sessions.
* `GET /api/admin/capacity` (platform admin) shows sessions, DB size per table, last retention run, last backup, AI usage, waitlist length.

## 3. Housekeeping jobs (worker)
**Session reaper** (every `REAPER_INTERVAL_SECONDS`, default 60): takes a Redis lock, then
* deletes sessions that never got a QR scan after `REAP_UNSCANNED_MINUTES` (10): gateway session + DB row, audit `instance.reaped`;
* stops (does not delete) *trial-plan* sessions idle for `REAP_TRIAL_IDLE_DAYS` (7): status `disconnected`, audit `instance.idle_stopped`. Paid plans are never touched;
* `REAPER_DRY_RUN=true` only logs what it would do. `REAPER_ENABLED=false` switches it off.

**Reconciler** (every 300 s): a gateway session `cf-*` with no DB row is deleted only when it is seen on **two consecutive runs**; a DB row whose session vanished is marked `disconnected` (if it was ever linked) or removed (if it never was), after a 180 s grace for brand-new rows.

**Retention** (daily at `RETENTION_HOUR_UTC`, default 03:00; on demand `make retention-now`): batched deletes (never one huge statement) of operational data only; `messages` and `leads` are never pruned.

| Table | Rule |
|---|---|
| `webhook_deliveries` | success/failed older than 30 d; payload blanked after 7 d; pending/retrying kept |
| `notifications` | read and older than 90 d |
| `audit_log` | older than 24 months |
| `usage_events` | older than 13 months, **rolled up first** into Redis `usage:rollup:<tenant>` (day -> count) |
| `drafts` | sent/discarded older than 180 d |
| `batches` | older than 12 months |

Result of the last run: Redis `ops:retention:last` (also in `/api/admin/capacity`).

## 4. Backups
What a set contains (`backups/<UTC stamp>/`, git-ignored): `connectflow.dump` and `openwa.dump` (`pg_dump -Fc`), `gateway-volume.tar.gz` (WhatsApp session keys + auth DB), `env.enc` (the `.env`, AES-256-CBC + PBKDF2 with `BACKUP_PASSPHRASE`), `SHA256SUMS`.
The gateway is stopped for about 10 s so the session files are consistent (`BACKUP_STOP_GATEWAY=0` skips that). Retention: newest 7 sets + the 4 newest Sunday sets. Optional offsite copy: set `RCLONE_REMOTE=remote:path` or `S3_BUCKET=s3://bucket/path`. On success it writes Redis `ops:backup:last`.

```bash
bash infra/scripts/backup.sh                  # or: make backup
bash infra/scripts/restore.sh backups/<stamp> --drill        # safe drill (see below)
BACKUP_PASSPHRASE=... bash infra/scripts/restore.sh backups/<stamp> --env recovered.env
bash infra/scripts/restore.sh backups/<stamp> --live         # DESTRUCTIVE: stops api/worker/gateway, replaces DBs + gateway volume
```
**Keep `BACKUP_PASSPHRASE` somewhere other than the server** (password manager). Without it `env.enc` is unreadable; without `.env` you lose the JWT and webhook secrets.

### Schedule
Linux / macOS cron (daily 02:30):
```
30 2 * * *  cd /opt/connectflow/infra && ./scripts/backup.sh >> ../backups/backup.log 2>&1
```
Windows Task Scheduler (Git Bash installed), run once in an elevated prompt:
```
schtasks /Create /SC DAILY /ST 02:30 /TN "ConnectFlow Backup" /TR "\"C:\Program Files\Git\bin\bash.exe\" -lc \"cd '/d/For sir/lead_gen_whatsapp/infra' && ./scripts/backup.sh >> ../backups/backup.log 2>&1\""
```
Alert if the backup is older than 26 h (section 6).

### Restore drill (do it monthly, and after changing the schema)
1. `bash infra/scripts/restore.sh backups/<newest> --drill`
2. It verifies `SHA256SUMS`, restores `connectflow.dump` into scratch database `cf_restore`, counts the rows **inside the dump file** and compares them with the restored tables (the live DB keeps changing, so its counts are shown for information only), then drops `cf_restore`.
3. Expected: `DRILL OK: every table restored with exactly the rows in the dump` (exit 0). Anything else exits non-zero: treat the backup as bad and take a new one.
4. Last drill on this machine: all 23 tables matched (leads 24, messages 15, users 4, tenants 4 at the time, audit_log 298 ...).

### Disaster recovery (new machine)
1. Install docker, clone the repo, copy the backup set over.
2. `restore.sh <set> --env infra/.env` (needs the passphrase) to get the secrets back.
3. `docker compose up -d postgres redis`, then `restore.sh <set> --live` (restores both databases and the gateway volume), then `docker compose up -d --build`.
4. Check `curl -s http://127.0.0.1:8080/api/health/ready` (host header `app.connectflow.localhost`) and `/api/admin/capacity`; WhatsApp numbers reconnect without a new QR if the volume was restored.

## 5. Monitoring
`docker compose --profile monitoring up -d uptime-kuma`, open http://127.0.0.1:3001, create the admin user, add monitors:

| Monitor | Type | Target | Alert when |
|---|---|---|---|
| API alive | HTTP | `http://api:8000/api/health` | down 2 checks |
| Platform ready | HTTP | `http://api:8000/api/health/ready` | non-200 (DB, Redis or **gateway down**) |
| Website | HTTP | public URL | down |
| Postgres | TCP | `postgres:5432` | down |
| Docker health | Docker container | worker, api, wa-gateway | `unhealthy` (the worker is unhealthy when its heartbeat is > 60 s old = **worker lag**) |
| Backup age | Push monitor | called at the end of `backup.sh` (add `curl -fsS $KUMA_PUSH_URL`) or heartbeat on `ops:backup:last` | no push for **26 h** |

Rules to wire (Kuma notifications: email/Telegram/Slack):
* **Gateway down**: "Platform ready" fails with `"gateway":"error"`.
* **Worker lag**: worker container unhealthy, or `redis-cli get ops:worker:heartbeat` older than 60 s.
* **Disk above 80 %**: host monitor (Kuma "Docker host" / node exporter). Postgres and `openwa-data` are the growers; `/api/admin/capacity` lists the biggest tables.
* **Failed-webhook rate**: `select status, count(*) from webhook_deliveries where created_at > now() - interval '1 hour' group by 1;` alert when failed / total > 20 % (customer endpoint problems show up here before they complain).
* **Backup older than 26 h**: `/api/admin/capacity` -> `backups.lastBackupAt`.

Errors: set `SENTRY_DSN` to enable Sentry (off by default). Events are scrubbed: no headers, cookies, bodies, query strings, user info (`send_default_pii=false`).

## 6. Quick triage
| Symptom | Look at |
|---|---|
| Users cannot create a number (503) | `/api/admin/capacity`: full? raise `GATEWAY_MAX_SESSIONS` + `PLATFORM_MAX_INSTANCES` + memory, then drain the waitlist |
| QR never appears | `docker compose logs wa-gateway`; gateway ready? sessions at cap? |
| Messages stuck queued | worker unhealthy? `docker compose logs worker`; outreach daily limits / active hours |
| `ready` returns 503 | the JSON names the failing dependency (`database`, `redis`, `gateway`) |
| A session disappeared | audit log `instance.reaped` / `instance.idle_stopped` shows who/what removed it |

## 7. Account requests and 2FA support (M5)

### Deleting a workspace on request (GDPR / "delete my data")
A workspace owner can press **Settings > Your data > Request workspace deletion** (password required). That only records the request and emails the owner and `ADMIN_EMAIL`: **nothing is deleted automatically.** To act on it:
1. List open requests: `docker compose exec postgres psql -U connectflow -d connectflow -c "select id, name, deletion_requested_at from tenants where deletion_requested_at is not null order by 3;"`
2. Confirm with the owner by email (reply from the address on file). Offer the data export first (Settings > Your data > Download my data, 3 per day).
3. Admin console > Customers > open the workspace > **Delete**. This removes its gateway sessions, the workspace, all of its rows (leads, messages, drafts, keys, webhooks ... by cascade) and users who belong to no other workspace. It is audited (`admin.customer.delete`). Platform-admin workspaces cannot be deleted here.
4. Tell the owner it is done. Data may remain in encrypted backups until they expire (30 days by default, see section 4).
The owner can withdraw the request any time (Settings > Your data > Cancel the deletion request).

### A user lost their authenticator app and their recovery codes
Verify who they are first (email on file, plus whatever your policy requires). Then clear their 2FA so they can sign in with the password and set it up again:
```sql
update users set totp_secret=null, totp_enabled_at=null, totp_last_step=null, recovery_codes='[]'::json where email='person@example.com';
insert into audit_log (at, tenant_id, user_id, action, detail) select now(), null, id, 'mfa.reset_by_support', 'cleared by an operator' from users where email='person@example.com';
```
Platform admins are sent straight to the enrolment screen at their next sign-in while `ADMIN_2FA_REQUIRED` is on (default in production).

### Related settings
`ADMIN_2FA_REQUIRED` (default: on when `COOKIE_SECURE=true`), `ENCRYPTION_KEY` / `ENCRYPTION_KEY_PREVIOUS` (docs/SECRETS.md), `DISPOSABLE_EMAIL_ALLOW`, `TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET_KEY`. Trial workspaces may start at most 3 WhatsApp sessions per hour.
