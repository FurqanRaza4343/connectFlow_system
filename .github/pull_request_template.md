## What and why

## Checklist
- [ ] `make test` / `make lint` pass
- [ ] No secrets, real phone numbers, emails or customer data added (`python infra/scripts/check-secrets.py --staged`)
- [ ] New env vars added to `infra/.env.example` (placeholder values only) and `docs/SECRETS.md`
- [ ] DB change has an Alembic migration (up and down tested)
