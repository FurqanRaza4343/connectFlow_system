# Developer shortcuts.  `make help` lists them.  Needs: docker compose, python 3.12 (for local tests), node 22 (for local builds).
SHELL := bash
PY ?= python

.PHONY: help up down-safe ps logs test lint e2e e2e-% backup restore-drill retention-now build-ui

help:
	@grep -E '^[a-zA-Z_%-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s $$'\t'

up: ## build + start the SaaS stack (never removes volumes)
	cd infra && docker compose up -d --build

ps: ## container status and health
	cd infra && docker compose ps

logs: ## follow api + worker logs (JSON)
	cd infra && docker compose logs -f --tail 100 api worker

lint: ## ruff on the backend
	cd backend && ruff check .

test: ## unit tests (no database or network needed)
	cd backend && $(PY) -m pytest

build-ui: ## typecheck + build the React app and the marketing site
	cd frontend && npm run build
	cd website && npm run build

e2e: ## all end-to-end suites against the running stack (set E2E_ADMIN_PASSWORD for the admin suites)
	$(PY) backend/tests/run_e2e.py

e2e-%: ## one suite: e2e-m2 | e2e-m3 | e2e-flow_m3 | e2e-m3_engine | e2e-m4 | e2e-security
	$(PY) backend/tests/run_e2e.py $*

backup: ## encrypted backup set in ./backups (needs BACKUP_PASSPHRASE in infra/.env)
	bash infra/scripts/backup.sh

restore-drill: ## restore the newest backup into scratch DB cf_restore and compare row counts (safe)
	bash infra/scripts/restore.sh "$$(ls -1d backups/20* | sort | tail -1)" --drill

retention-now: ## run the data-retention job once, now
	cd infra && docker compose exec -T worker python -m app.retention
