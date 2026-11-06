# Billing (Stripe)

ConnectFlow bills per **workspace**, monthly, in **US dollars** through Stripe Checkout. Card details never touch our servers. The plan only changes when Stripe's signed webhook says so, never from the browser redirect.

| Plan | Price | Numbers | Messages/day | AI drafts/day | Seats |
|---|---|---|---|---|---|
| Trial | free | **1** | 50 | 20 | 1 |
| Starter | $19 | 3 | 500 | 200 | 1 |
| Growth | $59 | 10 | 3,000 | 1,000 | 10 |

Limits live in the `plans` table (editable in the admin app, Plans). PKR prices in the UI are indicative only; Stripe charges USD. Without `STRIPE_SECRET_KEY` the old manual flow (request form, an admin approves) keeps working.

## 1. Test mode (do this first)
1. Create a Stripe account, stay in **Test mode** (toggle top right).
2. Developers → API keys → copy the **Secret key** `sk_test_...`.
3. Put it in `infra/.env` (never in Git, never in chat):
   ```
   STRIPE_SECRET_KEY=sk_test_...
   STRIPE_WEBHOOK_SECRET=whsec_...     # from step 4
   ```
4. Webhook secret, locally: install the [Stripe CLI](https://docs.stripe.com/stripe-cli), then
   ```bash
   stripe login
   stripe listen --forward-to http://app.connectflow.localhost:8080/api/stripe/webhook
   ```
   It prints `whsec_...`: use that value as `STRIPE_WEBHOOK_SECRET`. (The CLI sends the Host header it is given; if your OS cannot resolve `*.localhost`, forward to `http://127.0.0.1:8080/api/stripe/webhook` and add `--headers "Host: app.connectflow.localhost:8080"`.)
5. `cd infra && docker compose up -d api worker` (recreates them with the new keys).
6. In the app: Billing → Subscribe. Pay with the test card `4242 4242 4242 4242`, any future date, any CVC. A few seconds after returning, the page shows the new plan.
   Useful test cards: `4000 0000 0000 0341` (card attaches, then payment fails: exercises the past-due path), `4000 0025 0000 3155` (3-D Secure).
7. "Manage billing" opens the Stripe customer portal (change card, invoices, cancel).

Optional: create Products/Prices in Stripe and set `STRIPE_PRICE_STARTER` / `STRIPE_PRICE_GROWTH` (`price_...`). If absent, Checkout uses inline prices built from the plans table (amount = `plans.price_usd` in cents).

## 2. The webhook endpoint
`POST https://<app host>/api/stripe/webhook` (public; no cookie, no CSRF header; authenticated only by the `Stripe-Signature` HMAC with a 5-minute tolerance). Subscribe it to exactly these events:

`checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`, `invoice.paid`

Every event id is stored in `stripe_events`; a retried delivery is acknowledged (`duplicate: true`) and not applied twice. A processing failure returns 500 so Stripe retries.

## 3. How state maps to plans
| Stripe says | Workspace gets |
|---|---|
| subscription `active` / `trialing` | the purchased plan (from `metadata.plan_id`) |
| `past_due` / `invoice.payment_failed` | same plan for a **7-day grace**; the Billing page shows a banner with the end date; the worker (hourly) moves it to Trial afterwards |
| `invoice.paid` while past due | back to active, grace cleared |
| `canceled`, `unpaid`, `incomplete_expired`, subscription deleted | Trial |
| `cancel_at_period_end` | plan stays until the period ends; banner shown |

Switching plan while subscribed updates the existing subscription (proration) instead of creating a second one.

**Downgrades never delete data.** If the new plan allows fewer numbers than are linked, the oldest N stay usable and the rest are flagged `over_limit`: kept, visible, but they cannot send (API/outreach) and cannot be picked by the sender. Upgrading unflags them automatically. Adding a number is blocked while at or over the limit.

## 4. Go-live checklist
- [ ] Switch Stripe to Live mode; create the live **Secret key** and a **live webhook endpoint** with the six events above and your real HTTPS app host.
- [ ] Put `sk_live_...` and the live `whsec_...` in the production `infra/.env` (and nowhere else); `docker compose up -d api worker`.
- [ ] Domain + HTTPS (`COOKIE_SECURE=true`, real `APP_HOST`/`PUBLIC_APP_URL`): Checkout return URLs follow the host the owner is on.
- [ ] Stripe → Settings → Customer portal: enable cancel, update payment method, invoice history; set the business name and support email.
- [ ] Stripe → Settings → Emails: enable receipts and failed-payment emails (Stripe sends them, not us).
- [ ] Complete Stripe account activation (business details, bank payout). Check that your country is supported for charging in USD.
- [ ] Run one real $19 subscription with your own card, cancel it, and refund it from the dashboard to prove the loop.
- [ ] Taxes: enable Stripe Tax if you must collect VAT/sales tax. Terms/Privacy pages should mention billing and refunds.
- [ ] Rotate the keys if they were ever pasted into chat, tickets or screenshots (docs/SECRETS.md).

## 5. Refunds, cancellation and support hooks
- **Cancel:** the owner uses Manage billing. The subscription ends at period end (webhook → Trial); nothing is deleted.
- **Refund:** issue it in the Stripe dashboard (Payments → Refund). A refund does not by itself cancel the subscription; cancel it there too, which arrives as `customer.subscription.deleted`.
- **Manual override by support:** a platform admin can still set a workspace's plan in the admin app (this also flags numbers over the new limit). A later Stripe event for a subscribed workspace takes precedence.
- **Audit:** `billing.checkout`, `billing.subscribed`, `billing.payment_failed`, `billing.payment_recovered`, `billing.downgrade` appear in the audit log; the admin customer view shows the subscription status, renewal date and ids (never card data).

## 6. Tests
`backend/tests/test_stripe_units.py` (signature, encoding, limits) and `backend/tests/e2e/test_m5_billing.py` (a fake Stripe server: checkout/portal, signature failures, idempotency, plan mapping, grace, over-limit, isolation). The e2e needs the stack started with fake-Stripe settings (`STRIPE_API_BASE=http://host.docker.internal:<port>`), so it skips itself otherwise.

## 7. Known limits
- Events are applied in arrival order; an out-of-order older `subscription.updated` could briefly show stale data until the next event. Stripe retries and the next renewal corrects it.
- USD only. PKR/bank-transfer customers use the manual request flow while `STRIPE_SECRET_KEY` is empty, or ask support to set the plan by hand.
- Seat counts (Growth has 10) are a plan limit, not a per-seat price.
