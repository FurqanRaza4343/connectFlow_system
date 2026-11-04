"""Shared configuration for the end-to-end scripts. Everything comes from the environment; nothing here reads or prints secrets.

E2E_BASE_URL        default http://127.0.0.1:8080  (Caddy)
E2E_APP_HOST        default app.connectflow.localhost:8080  (Host header for the customer app)
E2E_ADMIN_HOST      default admin.connectflow.localhost:8080
E2E_INFRA_DIR       default <repo>/infra  (where `docker compose` runs for SQL helpers)
E2E_ADMIN_PASSWORD  platform admin password (only for admin tests; they SKIP without it)

Exit codes of every script: 0 = all checks passed, 1 = a check failed, 77 = skipped (precondition not met).
"""
import os
import pathlib
import subprocess
import sys

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8080")
APP_HOST = os.environ.get("E2E_APP_HOST", "app.connectflow.localhost:8080")
ADMIN_HOST = os.environ.get("E2E_ADMIN_HOST", "admin.connectflow.localhost:8080")
INFRA = os.environ.get("E2E_INFRA_DIR") or str(pathlib.Path(__file__).resolve().parents[3] / "infra")
SKIP = 77


def skip(reason: str):
    print(f"SKIP  {reason}")
    sys.exit(SKIP)


def api_env(*names: str) -> dict:
    """Values of env vars INSIDE the api container (never printed by callers)."""
    code = "import os,json;print(json.dumps({k:os.environ.get(k,'') for k in %r}))" % (list(names),)
    r = subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-c", code], cwd=INFRA, capture_output=True, text=True)
    import json
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {}


def require_admin_password() -> str:
    pw = os.environ.get("E2E_ADMIN_PASSWORD", "")
    if not pw:
        skip("E2E_ADMIN_PASSWORD not set (admin tests)")
    return pw


def require_mailpit():
    """Email-link checks read Mailpit, so they only make sense when mail is NOT routed to Resend."""
    if api_env("RESEND_API_KEY").get("RESEND_API_KEY"):
        skip("RESEND_API_KEY is set: mail goes to Resend, not Mailpit")


def require_fake_engine():
    e = api_env("AI_FAKE", "OUTREACH_FAKE_SEND")
    if not (e.get("AI_FAKE", "").lower() == "true" and e.get("OUTREACH_FAKE_SEND", "").lower() == "true"):
        skip("needs AI_FAKE=true and OUTREACH_FAKE_SEND=true on the api/worker")


def require_real_ai():
    """Suites that judge real model output (length, tone) are skipped when the fake AI is on or no provider key is configured."""
    v = api_env("AI_FAKE", "AI_API_KEY")
    if v.get("AI_FAKE", "").lower() == "true" or not v.get("AI_API_KEY"):
        skip("needs the real AI provider (AI_FAKE=false and AI_API_KEY set)")
