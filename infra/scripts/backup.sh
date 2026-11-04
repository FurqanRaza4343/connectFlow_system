#!/usr/bin/env bash
# ConnectFlow backup.  Usage: cd infra && ./scripts/backup.sh
# Produces backups/<UTC-stamp>/ with: connectflow.dump, openwa.dump (pg_dump -Fc), gateway-volume.tar.gz,
# env.enc (AES-256, needs BACKUP_PASSPHRASE), SHA256SUMS.  Keeps 7 daily + 4 weekly (Sunday) sets.
# Optional offsite: RCLONE_REMOTE=remote:path  or  S3_BUCKET=s3://bucket/path (aws cli).
# Env: BACKUP_DIR (default ../backups)  BACKUP_STOP_GATEWAY=1 (brief stop for a consistent session snapshot)
set -euo pipefail
cd "$(dirname "$0")/.."
# read only the keys we need from .env (it is not shell-safe: values may contain spaces/<>), never export the whole file
envget() { local v; v="$(grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-)"; v="${v%\"}"; v="${v#\"}"; echo "${!1:-$v}"; }
for k in BACKUP_PASSPHRASE POSTGRES_USER BACKUP_DIR BACKUP_STOP_GATEWAY RCLONE_REMOTE S3_BUCKET; do export "$k=$(envget $k)"; done
: "${BACKUP_PASSPHRASE:?set BACKUP_PASSPHRASE in infra/.env (encrypts the archived .env)}"
DC="docker compose"
ROOT="${BACKUP_DIR:-../backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/$STAMP"
mkdir -p "$OUT"
log() { echo "[backup $(date -u +%H:%M:%S)] $*"; }
PGU="${POSTGRES_USER:-connectflow}"

log "pg_dump connectflow"
$DC exec -T postgres pg_dump -U "$PGU" -Fc connectflow > "$OUT/connectflow.dump"
if $DC exec -T postgres psql -U "$PGU" -d postgres -Atc "select 1 from pg_database where datname='openwa'" | grep -q 1; then
  log "pg_dump openwa"
  $DC exec -T postgres pg_dump -U "$PGU" -Fc openwa > "$OUT/openwa.dump"
fi

# gateway volume (WhatsApp session keys + auth DB). Brief stop => consistent files.
STOPPED=0
if [ "${BACKUP_STOP_GATEWAY:-1}" = "1" ]; then
  log "stopping wa-gateway briefly"
  $DC stop wa-gateway >/dev/null && STOPPED=1
fi
trap '[ "$STOPPED" = "1" ] && $DC start wa-gateway >/dev/null || true' EXIT
VOL="$($DC config --format json 2>/dev/null | sed -n 's/.*"name": *"\([^"]*openwa-data\)".*/\1/p' | head -1)"
VOL="${VOL:-connectflow_openwa-data}"
log "archiving volume $VOL"
docker run --rm -v "$VOL":/data:ro -v "$(cd "$OUT" && pwd)":/out alpine tar czf /out/gateway-volume.tar.gz -C /data .
if [ "$STOPPED" = "1" ]; then $DC start wa-gateway >/dev/null; STOPPED=0; log "wa-gateway restarted"; fi

log "encrypting .env"
openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE -in .env -out "$OUT/env.enc"

(cd "$OUT" && sha256sum ./* | grep -v SHA256SUMS > SHA256SUMS)
SIZE=$(du -sk "$OUT" | cut -f1)

# retention: 7 newest dailies + 4 newest Sunday sets
cd "$ROOT"
ls -1d 20* 2>/dev/null | sort -r | tail -n +8 | while read -r d; do
  if [ "$(date -u -d "${d:0:8}" +%u 2>/dev/null)" != 7 ]; then rm -rf -- "$d"; fi
done || true
ls -1d 20* 2>/dev/null | sort -r | while read -r d; do if [ "$(date -u -d "${d:0:8}" +%u 2>/dev/null)" = 7 ]; then echo "$d"; fi; done | tail -n +5 | while read -r d; do rm -rf -- "$d"; done || true
cd - >/dev/null

if [ -n "${RCLONE_REMOTE:-}" ]; then log "rclone -> $RCLONE_REMOTE"; rclone copy "$OUT" "$RCLONE_REMOTE/$STAMP"; fi
if [ -n "${S3_BUCKET:-}" ]; then log "aws s3 -> $S3_BUCKET"; aws s3 cp --recursive "$OUT" "$S3_BUCKET/$STAMP"; fi

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
$DC exec -T redis redis-cli SET ops:backup:last "{\"at\":\"$NOW\",\"sizeBytes\":$((SIZE*1024))}" >/dev/null || log "WARN: could not record ops:backup:last"
log "done: $OUT (${SIZE} KB)"
