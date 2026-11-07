"""Run the end-to-end suites against a running stack (docker compose up).

    python backend/tests/run_e2e.py [name ...]        # names: m2 m3 m4 flow_m3 m3_engine security multinumber m5 m5_billing media media_cloudinary

Env (see tests/e2e/e2e_env.py): E2E_BASE_URL, E2E_APP_HOST, E2E_ADMIN_HOST, E2E_INFRA_DIR, E2E_ADMIN_PASSWORD,
E2E_DOCKER_NETWORK (default <compose project>_default), E2E_LOG_DIR (default backend/tests/e2e/logs).
Exit code: 0 = every suite passed or was skipped, 1 = at least one failed.  A suite that exits 77 is reported as SKIP.
Each suite cleans up only the data it created.  Nothing here reads or prints secrets.
"""
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
E2E = HERE / "e2e"
SUITES = {
    "m2": "e2e_m2.py",
    "m3": "e2e_m3.py",
    "flow_m3": "flow_m3.py",
    "m3_engine": "e2e_m3_engine.py",
    "m4": "e2e_m4.py",
    "security": "test_security.py",
    "multinumber": "test_multinumber.py",
    "m5": "test_m5_security.py",
    "m5_billing": "test_m5_billing.py",  # needs the fake-Stripe wrapper; skips itself otherwise
    "media": "test_media.py",  # needs AI_FAKE + OUTREACH_FAKE_SEND
    "media_cloudinary": "test_media_cloudinary.py",  # needs the api started with STORAGE_BACKEND=cloudinary against the fake Cloudinary (see docs/MEDIA.md); skips itself otherwise
}
LOGS = pathlib.Path(os.environ.get("E2E_LOG_DIR", E2E / "logs"))


def start_receiver() -> None:
    """Webhook sink used by e2e_m2 (reachable from the api container as http://cf-recv:9000)."""
    subprocess.run(["docker", "rm", "-f", "cf-recv"], capture_output=True)
    net = os.environ.get("E2E_DOCKER_NETWORK") or f"{os.environ.get('COMPOSE_PROJECT_NAME', 'connectflow')}_default"
    r = subprocess.run(
        ["docker", "run", "-d", "--name", "cf-recv", "--network", net, "--network-alias", "cf-recv", "-v", f"{E2E / 'recv.py'}:/recv.py:ro", "python:3.12-slim", "python", "/recv.py"],
        capture_output=True, text=True,
    )
    if r.returncode:
        print("WARN could not start cf-recv:", r.stderr.strip()[:200])
    time.sleep(1.5)


def reset_rate_limits() -> None:
    """Signups/logins are throttled per IP for an hour; every suite signs up several test tenants from the same address, so clear those counters first (test stacks only)."""
    infra = os.environ.get("E2E_INFRA_DIR") or str(HERE.parents[1] / "infra")
    lua = "for _,p in ipairs({'signup:*','login:*','login-ip:*','forgot:*','forgot-ip:*','reset:*','verify:*','mfa-ip:*'}) do for _,k in ipairs(redis.call('keys',p)) do redis.call('del',k) end end return 1"
    subprocess.run(["docker", "compose", "exec", "-T", "redis", "redis-cli", "eval", lua, "0"], cwd=infra, capture_output=True)


def main() -> int:
    wanted = sys.argv[1:] or list(SUITES)
    LOGS.mkdir(parents=True, exist_ok=True)
    start_receiver()
    results = []
    try:
        for key in wanted:
            f = E2E / SUITES[key]
            if not f.exists():
                results.append((key, "MISSING", 0))
                continue
            reset_rate_limits()
            t0 = time.time()
            p = subprocess.run([sys.executable, "-X", "utf8", str(f)], cwd=E2E, capture_output=True, text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            (LOGS / f"{key}.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8")
            status = {0: "PASS", 77: "SKIP"}.get(p.returncode, "FAIL")
            tail = [l for l in p.stdout.splitlines() if "passed," in l]
            results.append((key, status, round(time.time() - t0, 1), tail[-1].strip() if tail else ""))
            if status == "FAIL":
                print("\n".join(l for l in p.stdout.splitlines() if l.startswith("FAIL"))[:2000])
    finally:
        subprocess.run(["docker", "rm", "-f", "cf-recv"], capture_output=True)
    print("\n== e2e summary ==")
    for r in results:
        print(f"{r[0]:<10} {r[1]:<5} {r[2]:>6}s  {r[3] if len(r) > 3 else ''}")
    return 1 if any(r[1] in ("FAIL", "MISSING") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
