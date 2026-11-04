#!/bin/sh
# Archive (encrypted) then STOP the legacy stack. Never removes containers or data.
# Usage: sh infra/scripts/retire-legacy.sh [--dry-run]
set -eu
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="backups/legacy-$ts.tar.gz.enc"
dry=0; [ "${1:-}" = "--dry-run" ] && dry=1

[ -d legacy ] || { echo "no legacy/ folder - nothing to retire"; exit 0; }
echo "Will archive: legacy/  ($(du -sk legacy | cut -f1) KB)  ->  $out"
echo "Will run:     docker compose -f infra/docker-compose.yml --profile legacy stop"
if [ "$dry" = 1 ]; then echo "[dry-run] nothing done."; exit 0; fi

printf 'Type RETIRE to continue: '; read -r ans
[ "$ans" = "RETIRE" ] || { echo "aborted"; exit 1; }
command -v openssl >/dev/null || { echo "openssl required"; exit 1; }
mkdir -p backups
tar -czf - legacy | openssl enc -aes-256-cbc -pbkdf2 -salt -out "$out"
echo "verifying archive decrypts (enter the same passphrase)..."
openssl enc -d -aes-256-cbc -pbkdf2 -in "$out" | tar -tzf - >/dev/null
echo "archive OK: $out"
docker compose -f infra/docker-compose.yml --profile legacy stop
echo "legacy stopped (not removed). Roll back: docker compose -f infra/docker-compose.yml --profile legacy start"
