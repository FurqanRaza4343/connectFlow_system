# Secrets inventory and rotation

All live in `infra/.env` (git-ignored) locally, and in the host's secret store / GitHub Actions secrets in production. Placeholders are in `infra/.env.example`.

| Name | What it protects | Rotate how | Impact of rotation |
|---|---|---|---|
| `JWT_SECRET` / `SESSION_SECRET` | login cookies, signed tokens, keyed hashes of 2FA recovery codes | new `openssl rand -hex 32`, restart api | all users logged out; **2FA recovery codes stop working** (users keep their authenticator app and can generate new codes in Settings > Security) |
| `POSTGRES_PASSWORD` | app DB | `ALTER USER`, then update `.env`, restart api/worker/gateway | brief restart |
| `OPENWA_WEBHOOK_SECRET` | gateway -> api webhook auth | new value in `.env`, restart gateway + api | none |
| Gateway master/API key (`.api-key`, generated) | control of the WhatsApp gateway | delete generated key, restart gateway | api re-reads it |
| `RESEND_API_KEY` | sending email | create new key in Resend, revoke old | none |
| `AI_API_KEY` (DeepSeek) | AI drafts/research (costs money) | new key at provider, revoke old | none |
| `GOOGLE_CLIENT_SECRET` | Google sign-in | reset in Google Cloud console | none |
| `ADMIN_PASSWORD` | platform admin login | change in admin UI, update `.env` | none |
| `BACKUP_PASSPHRASE` | decrypts backups | re-encrypt a fresh backup with new passphrase; keep old until old sets expire | old sets need old passphrase |
| `SENTRY_DSN` (if used) | error reporting | regenerate in Sentry | none |
| `ENCRYPTION_KEY` | webhook signing secrets and 2FA secrets at rest (Fernet) | see **Rotating ENCRYPTION_KEY** below | none if done in order; losing the key makes those secrets unreadable |
| `TURNSTILE_SECRET_KEY` (optional) | Cloudflare captcha verification | create new widget secret in Cloudflare, update `.env` | none |
| `CLOUDINARY_URL` (only with `STORAGE_BACKEND=cloudinary`) | the whole media library at Cloudinary (`cloudinary://<api_key>:<api_secret>@<cloud>`) | Cloudinary console: regenerate the API secret, put the new URL in `infra/.env`, `docker compose up -d api worker` | none (files stay; uploads fail until the new URL is set). **Secret: never in Git, never in chat, never in logs** |

## Rotate NOW (before first push) if any of these ever left your machine
Any key you pasted in chat/screenshots/Notion, in particular the DeepSeek key (it appeared in a screenshot referenced by the legacy README) and the Resend key if it was ever shown. Rotating is free; a leaked key is not.

## Rules
- Never commit values. Use env vars; add new names to `.env.example` with a placeholder.
- Customer API keys (`cf_live_...`) are stored only as SHA-256 hashes; they cannot be recovered, only revoked.
- `python infra/scripts/check-secrets.py` compares every file against the real values in your local `.env` files, so a pasted secret is caught even if it matches no known pattern.


## Rotating ENCRYPTION_KEY
`ENCRYPTION_KEY` encrypts, at rest, every webhook signing secret and every two-factor secret (stored as `enc1:...`). In production (`COOKIE_SECURE=true`) the api refuses to start without it. Without it (development) a key is derived from `JWT_SECRET`.

Generate a key (urlsafe base64 of 32 random bytes):
```bash
docker compose exec api python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Rotate with no downtime and no lost data:
1. Put the **current** key into `ENCRYPTION_KEY_PREVIOUS` and the **new** key into `ENCRYPTION_KEY` in `infra/.env` (if you never set one, only set `ENCRYPTION_KEY`: the key derived from `JWT_SECRET` is always tried as a fallback).
2. `cd infra && docker compose up -d --force-recreate api worker`. Everything still opens (new writes use the new key, old rows open with the previous one).
3. Re-encrypt every stored secret with the new key: `docker compose exec api python -m app.tools.reencrypt` (prints `{'reencrypted': N, 'failed': 0}`). `failed` must be 0.
4. Check: `docker compose exec api python -m app.tools.reencrypt --check` shows `webhookSecretsPlaintext: 0`.
5. Remove `ENCRYPTION_KEY_PREVIOUS` from `infra/.env` and `docker compose up -d --force-recreate api worker`. Destroy the old key.

Back the key up with your other secrets (it is **not** inside the database backup). If it is lost, webhook signing secrets cannot be decrypted (customers must rotate each endpoint secret: deliveries fail with a clear "cannot be decrypted" error rather than being sent unsigned) and 2FA authenticator secrets cannot be read (users sign in with a recovery code, then set 2FA up again).
