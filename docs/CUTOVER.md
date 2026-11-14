# Retiring the legacy One Group stack

Do this only after: One Group number linked in the `deonegroup.com` workspace (QR scanned), a test message sent and received, and `docs/M4_PARITY.md` has no open items you need.

1. `make backup` (new stack) and keep the passphrase safe.
2. Dry run: `sh infra/scripts/retire-legacy.sh --dry-run` (Windows: `infra\scripts\retire-legacy.ps1 -DryRun`). Prints exactly what it will archive and stop.
3. Real run: `sh infra/scripts/retire-legacy.sh` - you must type `RETIRE`. It:
   - encrypts `legacy/` into `backups/legacy-<timestamp>.tar.gz.enc` (AES-256, passphrase prompt) and verifies it decrypts and lists,
   - then **stops** (never removes) `openwa`, `orchestrator` via `docker compose --profile legacy stop`.
4. Keep the stopped containers and the encrypted archive for 30 days. Roll back with `docker compose --profile legacy start`.
5. After 30 days: copy the archive off-machine, then delete `legacy/` yourself.

`legacy/` is git-ignored and never pushed.

## Data import from the old dashboard (before you retire it)

The importer moves the leads, lists, conversation log and open drafts, **and now also the settings, the company knowledge document, the operator's extra style rules and the branding** into the `deonegroup.com` workspace. It never moves the WhatsApp session (relink the number by QR), and it never copies the legacy "live" switches: the workspace always starts with `auto_run = false` and `dry_run = true`.

```bash
cd infra
# 1. dry run (writes nothing); the old orchestrator may keep running, the file is opened read-only
docker compose run --rm --no-deps -v "$PWD/../legacy:/legacy:ro" api python -m app.tools.migrate_legacy   --state /legacy/orchestrator/state/state.sqlite --knowledge /legacy/data/company_knowledge.md   --tenant "deonegroup.com" --company-name "One Group" --website "https://deonegroup.com" --backfill
# 2. the same command with --apply
```
- `--backfill` also fills empty fields (country, enrichment, rating note) of leads imported by an older version of this tool.
- Safe to run again: nothing is duplicated.
- Printed at the end: what was **not** migrated (admin WhatsApp notification numbers, notifications, batches history, sent/discarded drafts, the Status test posts).
- Take a database backup first (`make backup` or `pg_dump -Fc`). Tests: `python backend/tests/run_e2e.py migrate_legacy` and `backend/tests/test_migrate_legacy_units.py`.
