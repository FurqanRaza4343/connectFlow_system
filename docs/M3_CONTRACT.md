# M3 — Lead-gen engine: API contract (backend <-> frontend)

All routes are under `/api`, cookie auth + `X-Requested-With: ConnectFlow` on non-GET (see `frontend/src/api.ts`). Roles: viewer = read, operator = write, admin/owner = settings. Every query is scoped to the caller's tenant. JSON keys below are exactly what the backend returns.

## Behaviour (ported from `legacy/orchestrator`, per tenant)
- **Eligible lead** = status `new` AND `whatsapp != ''`.
- **Batch** (scheduler or "Run now"): take `min(batch_size, daily_limit - sent_today - pending_first_drafts)` eligible leads. For each: research (crawl the lead's website + SearXNG when missing, then an AI summary stored in `leads.research`) -> AI writes the first message using the profile (company, sender, offer, knowledge, tone) -> creates a `drafts` row (kind `first`, status `pending`, source `ai`; AI failure -> fallback template, note "AI unavailable", never auto-sent). Lead -> `queued`.
- **dry_run = true (default)**: drafts are created and visible, but approving/sending is blocked (409 "Dry-run is ON"). **require_approval = true (default)**: drafts wait for approval. If `require_approval=false` and `dry_run=false`, ready drafts are sent without approval.
- **Sender** (worker, one message at a time per tenant): approved drafts go out with a random gap `delay_min..delay_max` seconds. Per message: lead still sendable (not stop/contacted), number exists on WhatsApp (`check_number`), tenant daily first-message cap (`daily_limit`), plan quota (`quota.reserve_message`), per-number **warm-up** cap by number age in days `[5,10,15,20,30,40]` (last value repeats), choose the ready instance with most remaining cap. Draft status machine: `pending -> approved -> sending -> sent | failed`; `discarded`. On restart `sending` -> `failed` ("check WhatsApp before resending"), `approved` resume. Never send a second first message to the same number (idempotency guard on `messages`). Lead -> `contacted` (`sent_at`, `instance_id`, `first_message`).
- **Inbound reply** (gateway webhook): match lead by chat id / number; store `messages(in)`; strict STOP (<= 60 chars and keyword/phrase) -> lead `stop`, discard its pending drafts, notify; else lead `replied`, AI classifies intent + drafts a reply (`kind=reply`; pricing/complaint => `needsHuman`, note "NEEDS HUMAN: pricing"). `auto_reply=true` and not needsHuman and not dry_run -> sent after a 15-45 s human delay, otherwise a pending reply draft + notification. Also emit the existing customer webhook `message.received`.
- **Follow-ups** (when `followups_enabled`): a `contacted` lead with no reply and `followup_stage=0` gets a follow-up draft (kind `followup`) once `followup_day1` days passed since `sent_at`; stage 1 after `followup_day2` days; stops on reply/STOP/hold. Same approval rules.
- **Scheduler** (worker loop): every 30 s, per tenant with `auto_run=true`: inside active hours (`active_from..active_to` in `timezone`) and `now >= last_batch + interval_hours` -> run a batch. `auto_run=false` = paused (default).
- Notifications (in-app): types `batch`, `reply`, `needs_human`, `failed`, `session`, `info`.
- Dev/test switches (default off, env): `AI_FAKE=true` (deterministic AI text), `OUTREACH_FAKE_SEND=true` (sender marks sent without calling the gateway).
- Message CONTENT is visible to the customer only; admin APIs expose counts, never bodies.

## Profile (extends existing `GET/PUT /api/outreach/profile`)
New fields (snake_case, same as existing ones): `auto_run:bool=false`, `batch_size:int=5`, `interval_hours:int=5`, `delay_min:int=60`, `delay_max:int=180`, `dry_run:bool=true`, `auto_reply:bool=false`, `hold_pricing:bool=true`, `followups_enabled:bool=false`, `followup_day1:int=3`, `followup_day2:int=7`, `fallback_template:str=""`. Validation: delay_min<=delay_max, intervals >=1.

## Leads additions
- `POST /leads/{id}/research` -> lead (runs enrichment + research now; <= 60 s)
- `POST /leads/{id}/draft` -> draft (409 if no number or already contacted)
- `GET /leads/{id}/thread` -> `{messages:[{id,direction:'in'|'out',body,kind,at}], drafts:[draft...]}`
- `POST /leads/{id}/reply` body `{text}` -> draft (a manual reply, approved and sent through the sender; 409 in dry-run)
- `POST /leads/enrich` body `{ids?:[string], all?:bool}` -> `{queued:int}` (background "Improve data": website/contacts/socials) ; `GET /leads/enrich/status` -> `{running:bool, done:int, total:int}`
- lead objects gain: `enriched:bool`, `instanceId|null`, `sentAt|null`, `followupStage:int`

## Drafts
Draft object: `{id, leadId, company, whatsapp, kind:'first'|'followup'|'reply', body, incoming:string, status, note, source:'ai'|'manual'|'fallback', needsHuman:bool, createdAt}`
- `GET /drafts?status=open|pending|approved|sent|discarded|failed&kind=&page=1` -> `{rows:[draft], total, page, pages, counts:{pending,approved,sending,failed,sent,discarded}}` (`open` = pending+approved+sending+failed)
- `PATCH /drafts/{id}` `{body}` (only pending/failed) -> draft
- `POST /drafts/{id}/regenerate` `{instruction}` -> draft (AI rewrite)
- `POST /drafts/approve` `{ids:[...]}` -> `{approved:int}` (409 in dry-run)
- `POST /drafts/{id}/discard` -> `{ok:true}` ; `POST /drafts/cancel-sending` -> `{reset:int}`
- `GET /drafts/queue` -> `{sending:bool, remaining:int, done:int, nextAt:string|null, current:string|null}`

## Outreach status / batches
- `GET /outreach/status` -> `{autoRun, dryRun, requireApproval, inActiveHours, nextRunAt|null, lastBatch:{id,at,processed,queued,sent,failed}|null, sentToday, dailyLimit, eligibleLeads, instances:[{id,label,status,phone,warmupCap,sentToday}]}`
- `POST /outreach/run-now` `{limit?}` -> `{batchId, processed, queued, sent, failed}`
- `GET /outreach/batches` -> `[{id,startedAt,finishedAt,mode,processed,queued,sent,failed,error}]`

## Overview
`GET /overview` -> `{counts:{total,new,queued,contacted,replied,stop,no_whatsapp,failed,hold,verify,withNumber,enriched}, sentToday, dailyLimit, replyRate:int, pendingApprovals:int, needsHuman:int, unreadNotifications:int, daily:[{day,sent,replies}] (14 days), scheduler:<same as /outreach/status>, recent:[{at,type,title,body,leadId|null}] (20)}`

## Notifications
- `GET /notifications` -> `{rows:[{id,type,title,body,link,leadId,read:bool,createdAt}], unread:int}`
- `POST /notifications/read` `{ids?:[...]}` (omit ids = all) -> `{ok:true}`

## Inbox
`GET /inbox?filter=all|needs_human|replied` -> `[{leadId,company,whatsapp,status,lastMessage,lastDirection,lastAt,needsHuman:bool}]` (newest first, 100 max)

---

# M4 additions — multi-number control, number-aware conversations, e-mail alerts (migration 0008)

## Per-number control
- `instances.outreach_enabled` (bool, default true). `GET /instances` items gain `outreachEnabled`.
- `PATCH /instances/{id}` `{outreach_enabled: bool}` (owner/admin, audited as `instance.outreach`) -> the instance. Other tenants get 404.
- The outreach sender **never** picks a number with `outreach_enabled=false` (API-only; `/v1/messages` is unaffected). Replies/follow-ups already pinned to such a number still go out from it.
- `POST /drafts/approve` -> 409 "Outreach is switched off for every connected number (they are API-only)…" when every ready number is API-only (skipped under `OUTREACH_FAKE_SEND`).
- `instances[]` in `/outreach/status` and `/overview.scheduler` gain `sent7d` (all outbound, 7 days), `replies7d` (inbound, 7 days), `outreachEnabled`; `sentToday`/`warmupCap` unchanged (first messages vs warm-up cap).

## Conversations know their number
`via` = `{id,label,phone,status} | null`.
- `GET /inbox?filter=…&instance_id=` rows gain `instanceId`, `via` (conversation number = `lead.instance_id`, else the last message's number).
- `GET /leads?…&instance_id=` filter; each lead gains `via` (existing `instanceId` stays).
- `GET /leads/{id}/thread` -> `{messages:[…, instanceId], drafts:[…], via}`.
- Draft objects gain `via` (pinned number for `reply`/`followup`; `null` for `first` — its number is chosen at send time). Used by `GET /drafts` and the thread.
- Replies and follow-ups are **pinned** to the conversation's number (no silent fallback). If that number is not `ready` the draft stays `pending`/`approved` with note `Number +92… is disconnected - reconnect it to reply`. An inbound message on a lead without a number sets `lead.instance_id`.

## Profile — notifications (`GET/PUT /outreach/profile`)
New fields: `notify_email: bool` (default true), `notify_extra_emails: string` (comma separated, ≤3, validated; 422 otherwise; stored normalised), `notify_digest: bool` (default false).

## E-mail alerts (`app/alerts.py`)
Recipients = owner + admins + extras. Events: needs-human reply (or AI unavailable), a number that was `ready` goes down / needs a new QR, batch finished with failures, daily cap reached. Throttle: 1 e-mail / hour per (workspace, type) via Redis key `og:alert:{tenant}:{type}`. Previews ≤140 chars; footer points to Outreach > Setup > Notifications. Optional daily digest at 08:00 workspace time (worker `digest_loop`, once per local day via `og:digest:{tenant}:{date}`).

## Admin support actions (platform admin only, audited, never return tokens)
- `POST /admin/users/{id}/resend-verification` (409 if already verified) -> `{ok}`; audit `admin.user.resend_verification`.
- `POST /admin/users/{id}/send-password-reset` -> `{ok}`; audit `admin.user.send_reset`. Both: 429 if repeated within 2 minutes for the same user.
- `GET /admin/customers/{id}` `instances[]` gain `outreachEnabled`, `sentToday`, `sent7d`, `replies7d` (counters only, no message content).
