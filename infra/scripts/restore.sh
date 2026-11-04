#!/usr/bin/env bash
# Restore a backup set.  Usage:
#   ./scripts/restore.sh <backup-dir> --drill        restore into scratch DB cf_restore and compare row counts (safe, default)
#   ./scripts/restore.sh <backup-dir> --live         DESTRUCTIVE: replaces connectflow (and openwa) DBs; type YES to confirm
#   ./scripts/restore.sh <backup-dir> --env out.env  decrypt the archived .env (needs BACKUP_PASSPHRASE)
set -euo pipefail
cd "$(dirname "$0")/.."
# read only the keys we need from .env (it is not shell-safe: values may contain spaces/<>), never export the whole file
envget() { local v; v="$(grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-)"; v="${v%\"}"; v="${v#\"}"; echo "${!1:-$v}"; }
for k in BACKUP_PASSPHRASE POSTGRES_USER; do export "$k=$(envget $k)"; done
DC="docker compose"; PGU="${POSTGRES_USER:-connectflow}"
SET="${1:?backup dir}"; MODE="${2:---drill}"
[ -f "$SET/connectflow.dump" ] || { echo "no connectflow.dump in $SET"; exit 2; }
(cd "$SET" && sha256sum -c SHA256SUMS >/dev/null) || { echo "CHECKSUM MISMATCH - backup is corrupt"; exit 3; }
echo "checksums ok"
psql_() { $DC exec -T postgres psql -U "$PGU" -v ON_ERROR_STOP=1 "$@"; }

case "$MODE" in
--env)
  openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE -in "$SET/env.enc" -out "${3:?output file}"; echo "wrote ${3}" ;;
--drill)
  psql_ -d postgres -c "DROP DATABASE IF EXISTS cf_restore" -c "CREATE DATABASE cf_restore" >/dev/null
  $DC exec -T postgres pg_restore -U "$PGU" -d cf_restore --no-owner < "$SET/connectflow.dump"
  # Truth = rows inside the dump file itself (the live DB keeps changing, so it is only shown for information).
  $DC exec -T postgres pg_restore -U "$PGU" --data-only -f - < "$SET/connectflow.dump"     | awk '/^COPY /{t=$2; sub(/^public\./,"",t); n=0; inb=1; next} inb && /^\\.$/{c[t]=n; inb=0; next} inb{n++} END{for(k in c) print k, c[k]}' | sort > "$SET/.dump-counts"
  fail=0
  printf "  %-26s %8s %8s %8s
" table dump restored "live(info)"
  for t in $(psql_ -d cf_restore -Atc "select tablename from pg_tables where schemaname='public' order by 1"); do
    d=$(awk -v t="$t" '$1==t{print $2}' "$SET/.dump-counts"); d=${d:-0}
    b=$(psql_ -d cf_restore -Atc "select count(*) from \"$t\""); l=$(psql_ -d connectflow -Atc "select count(*) from \"$t\"" 2>/dev/null || echo "?")
    if [ "$d" = "$b" ]; then m=""; else m="  <-- MISMATCH"; fail=1; fi
    printf "  %-26s %8s %8s %8s%s
" "$t" "$d" "$b" "$l" "$m"
  done
  rm -f "$SET/.dump-counts"
  psql_ -d postgres -c "DROP DATABASE cf_restore" >/dev/null
  if [ $fail = 0 ]; then echo "DRILL OK: every table restored with exactly the rows in the dump"; else echo "DRILL FAILED"; exit 4; fi
  ;;
--live)
  read -r -p "This REPLACES the live databases from $SET. Type YES: " ok; [ "$ok" = YES ] || exit 1
  $DC stop api worker wa-gateway
  psql_ -d postgres -c "DROP DATABASE IF EXISTS connectflow WITH (FORCE)" -c "CREATE DATABASE connectflow OWNER $PGU"
  $DC exec -T postgres pg_restore -U "$PGU" -d connectflow --no-owner < "$SET/connectflow.dump"
  if [ -f "$SET/openwa.dump" ]; then
    psql_ -d postgres -c "DROP DATABASE IF EXISTS openwa WITH (FORCE)" -c "CREATE DATABASE openwa OWNER openwa"
    $DC exec -T postgres pg_restore -U "$PGU" -d openwa --no-owner --role=openwa < "$SET/openwa.dump"
  fi
  if [ -f "$SET/gateway-volume.tar.gz" ]; then
    docker run --rm -v connectflow_openwa-data:/data -v "$(cd "$SET" && pwd)":/in alpine sh -c 'rm -rf /data/* /data/.[!.]* 2>/dev/null; tar xzf /in/gateway-volume.tar.gz -C /data'
  fi
  $DC up -d api worker wa-gateway; echo "restored. Verify: curl -s http://127.0.0.1:8080/api/health/ready" ;;
*) echo "unknown mode $MODE"; exit 2 ;;
esac
