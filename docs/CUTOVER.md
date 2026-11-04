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
