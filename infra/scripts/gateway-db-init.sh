#!/bin/sh
# One-shot, idempotent: creates the `openwa` role + database on the shared Postgres for the WhatsApp gateway.
# Runs before wa-gateway starts (compose service gateway-db-init). Safe to run on every `up`.
set -eu
: "${PGPASSWORD:?}" "${OPENWA_DB_PASSWORD:?}"
psql -h "${PGHOST:-postgres}" -U "${PGUSER:-connectflow}" -d postgres -v ON_ERROR_STOP=1 -v pw="$OPENWA_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE openwa LOGIN PASSWORD %L', :'pw') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'openwa') \gexec
SELECT format('ALTER ROLE openwa PASSWORD %L', :'pw') \gexec
SELECT 'CREATE DATABASE openwa OWNER openwa' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'openwa') \gexec
SQL
echo "gateway database ready"
