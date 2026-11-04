# Secrets inventory and rotation

All live in `infra/.env` (git-ignored) locally, and in the host's secret store / GitHub Actions secrets in production. Placeholders are in `infra/.env.example`.

| Name | What it protects | Rotate how | Impact of rotation |
|---|---|---|---|
| `JWT_SECRET` / `SESSION_SECRET` | login cookies, signed tokens | new `openssl rand -hex 32`, restart api | all users logged out |
| `POSTGRES_PASSWORD` | app DB | `ALTER USER`, then update `.env`, restart api/worker/gateway | brief restart |
| `OPENWA_WEBHOOK_SECRET` | gateway -> api webhook auth | new value in `.env`, restart gateway + api | none |
| Gateway master/API key (`.api-key`, generated) | control of the WhatsApp gateway | delete generated key, restart gateway | api re-reads it |
| `RESEND_API_KEY` | sending email | create new key in Resend, revoke old | none |
| `AI_API_KEY` (DeepSeek) | AI drafts/research (costs money) | new key at provider, revoke old | none |
| `GOOGLE_CLIENT_SECRET` | Google sign-in | reset in Google Cloud console | none |
| `ADMIN_PASSWORD` | platform admin login | change in admin UI, update `.env` | none |
| `BACKUP_PASSPHRASE` | decrypts backups | re-encrypt a fresh backup with new passphrase; keep old until old sets expire | old sets need old passphrase |
| `SENTRY_DSN` (if used) | error reporting | regenerate in Sentry | none |

## Rotate NOW (before first push) if any of these ever left your machine
Any key you pasted in chat/screenshots/Notion, in particular the DeepSeek key (it appeared in a screenshot referenced by the legacy README) and the Resend key if it was ever shown. Rotating is free; a leaked key is not.

## Rules
- Never commit values. Use env vars; add new names to `.env.example` with a placeholder.
- Customer API keys (`cf_live_...`) are stored only as SHA-256 hashes; they cannot be recovered, only revoked.
- `python infra/scripts/check-secrets.py` compares every file against the real values in your local `.env` files, so a pasted secret is caught even if it matches no known pattern.
