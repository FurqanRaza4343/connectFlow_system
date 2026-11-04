# Security policy

Please report vulnerabilities privately (GitHub "Report a vulnerability" on this repo, or email the maintainer). Do not open a public issue for security problems.

- Secrets live only in `infra/.env` (git-ignored) and the deploy host's secret store. See `docs/SECRETS.md`.
- If you find a committed secret, tell the maintainer immediately; it will be rotated, not just deleted.
- Scope: the ConnectFlow API, dashboards, website and infra files in this repository.
