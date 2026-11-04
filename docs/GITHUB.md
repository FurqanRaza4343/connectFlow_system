# Pushing ConnectFlow to GitHub (one private monorepo)

## Why ONE repo
backend, frontend, website, infra and docs ship together: they share Alembic migrations, the API contract, one `docker-compose.yml` and one CI. Splitting would mean version-matching three repos for every change. Secrets and customer data are not a reason to split: they are never in git at all.

Create it as **Private**. Open-sourcing later is a separate decision (licence, ToS text, branding).

## What is NOT in git (all enforced by `.gitignore` + the scanner)
| Stays on your machine / server only | Why |
|---|---|
| `infra/.env`, any `.env*` except `*.env.example` | all real secrets |
| `legacy/` (whole folder) | real prospects (`leads.csv`), WhatsApp login sessions, `.api-key`, SQLite, old `.env` |
| `backups/` | encrypted DB + gateway session sets |
| `vendor/` | third-party clone with its own `.git` |
| `installers/` | 600 MB Docker installer |
| `node_modules/`, `dist/`, `__pycache__/`, `*.log`, `scratchpad/` | build/runtime noise |

Keep `legacy/` and `backups/` in a private encrypted archive (see `docs/CUTOVER.md`), never in the repo.

## Before the first push - IMPORTANT
This folder has a **stale git index** (42 staged entries from earlier, including the 600 MB Docker installer) and `.git` is ~612 MB with zero commits. Do not just `git add .`. Start clean (you run these, nothing here does it for you):

```bash
cd "d:/For sir/lead_gen_whatsapp"
rm -rf .git                      # safe: there are no commits to lose
git init -b main
sh infra/scripts/install-hooks.sh      # Windows: powershell infra/scripts/install-hooks.ps1
git add -A
python infra/scripts/check-secrets.py --staged      # must print 0 error(s)
git status --short | head -50                        # eyeball: no legacy/, backups/, .env, *.exe
git commit -m "Initial import: ConnectFlow monorepo"
git remote add origin git@github.com:<you>/connectflow.git
git push -u origin main
```
If you prefer not to delete `.git`: `git rm -r -q --cached . && git gc --prune=now` then continue from `git add -A`.

Expected size: ~230 files, ~2 MB.

## GitHub settings (once, on the repo)
- Settings > Code security: enable **Secret scanning** + **Push protection**, Dependabot alerts, Private vulnerability reporting.
- Branch protection on `main`: require PR, require checks `ci` and `secret-scan`.
- Actions secrets (only if you deploy from CI): add via Settings > Secrets, never in files. See `docs/SECRETS.md` for names.
- Do not enable GitHub Pages for this repo.

## Reviewed scanner exceptions
`.secrets-allow` lists the 3 reviewed false positives (dev DB URL default, an HTML `autocomplete` attribute, a phone number used as a test vector). One of them is a real-looking phone number in `backend/tests/test_units.py:45`; replace it with `+15550000001` when convenient and delete the allow line.

## If a secret is pushed by mistake
1. **Rotate it first** (docs/SECRETS.md). Deleting the commit does not un-leak it.
2. Remove from history: `git filter-repo --path <file> --invert-paths` (or BFG), force-push, ask GitHub support to purge cached views.
3. Add the pattern to `check-secrets.py` so it cannot recur.
