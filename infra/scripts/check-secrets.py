#!/usr/bin/env python3
"""check-secrets: refuse to let secrets or personal data reach git. Stdlib only.

Usage:
  python infra/scripts/check-secrets.py              # files git would add (tracked + untracked, not ignored)
  python infra/scripts/check-secrets.py --staged     # only what is staged (used by the pre-commit hook)
  python infra/scripts/check-secrets.py --all-files  # walk the tree ignoring git (paranoid mode)
  python infra/scripts/check-secrets.py --strict     # warnings (emails) also fail

Exit code 0 = clean, 1 = findings, 2 = usage/git error.
It NEVER prints a secret value: only file, line, and the rule that matched.
Allow-list: .secrets-allow at the repo root, one entry per line:  <glob>   or   <glob>|<rule>
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 2_000_000

# Paths that must never be in the repo, whatever their content.
FORBIDDEN_PATH = [
    (r"(^|/)legacy/", "legacy/ stack (real prospects + WhatsApp sessions) must stay out of git"),
    (r"(^|/)vendor/", "vendor/ third-party clones must stay out of git"),
    (r"(^|/)backups?/", "backup sets must stay out of git"),
    (r"(^|/)installers/", "installers/binaries must stay out of git"),
    (r"(^|/)\.env($|\.)(?!example$)", "real .env file (only *.env.example is allowed)"),
    (r"\.(sqlite3?|db|dump|bak|enc|pem|p12|pfx|jks|keystore)$", "database / key / backup file"),
    (r"(^|/)\.api-key$", "gateway API key file"),
    (r"\.(exe|msi|dmg|tar|tgz|zip|7z|rar)$|\.tar\.gz$", "binary archive / installer"),
    (r"(^|/)(sessions|baileys)/", "WhatsApp session data"),
    (r"(^|/)id_(rsa|ed25519)", "ssh private key"),
]

# (rule, regex, severity)
PATTERNS = [
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY"), "error"),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "error"),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "error"),
    ("resend-key", re.compile(r"\bre_[A-Za-z0-9]{8,}_[A-Za-z0-9]{16,}\b"), "error"),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"), "error"),
    ("connectflow-api-key", re.compile(r"\bcf_live_(?!x{8})[A-Za-z0-9_-]{20,}\b"), "error"),
    ("google-client-secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{16,}\b"), "error"),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "error"),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "error"),
    ("stripe-live-key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{16,}\b"), "error"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "error"),
    ("url-with-password", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@'\"]+:(?!\$|\{|<|changeme|password|secret|x{3,})[^\s/@'\"$]{6,}@(?!localhost|127\.0\.0\.1)[\w.-]+"), "error"),
    ("hardcoded-credential", re.compile(r"""(?i)\b(?:password|passwd|secret|api[_-]?key|token|passphrase)\b["']?\s*[:=]\s*["'](?![^"']*(?:\$\{|\$\(|<|\{\{|example|changeme|your[_-]|xxx|\.\.\.|placeholder|test|dummy|fake|secret-here))[A-Za-z0-9+/_\-=.]{16,}["']"""), "error"),
    ("phone-e164", re.compile(r"(?<![\w.])\+\d{10,14}(?!\d)"), "error"),
    ("email-address", re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b"), "warn"),
]

SAFE_PHONE = re.compile(r"^\+(?:1555\d{7}|44(?:7700|20)\d+|920{9,}|1{10,}|1234567\d*|9230000\d+|9230012345\d*|\d)$")
SAFE_EMAIL_DOMAINS = (
    "example.com", "example.org", "example.net", "resend.dev", "localhost", "connectflow.localhost",
    "users.noreply.github.com", "noreply.github.com", "anthropic.com", "mailpit", "test", "invalid", "local",
)
SAFE_EMAIL_LOCALS = ("noreply", "no-reply", "admin", "support", "security", "hello", "owner", "user", "you", "name", "email", "me", "a", "b", "x")

SECRET_KEY_NAME = re.compile(r"(SECRET|PASSWORD|PASSWD|PASSPHRASE|TOKEN|API_?KEY|PRIVATE|DSN|CREDENTIAL|_KEY$|^KEY$)", re.I)
PLACEHOLDER = re.compile(r"^(|changeme.*|change[-_ ]me.*|your[-_].*|<.*>|x+|\*+|true|false|null|none|0|1|dev|test|localhost|.{0,7})$", re.I)


def run_git(*args: str) -> list[str]:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"check-secrets: git failed: {e}", file=sys.stderr)
        sys.exit(2)
    return [p for p in out.split("\n") if p]


def candidate_files(mode: str) -> list[str]:
    if mode == "staged":
        return run_git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    if mode == "all":
        skip = {".git", "node_modules", "__pycache__", ".astro", "dist"}
        found: list[str] = []
        for dp, dns, fns in os.walk(ROOT):
            dns[:] = [d for d in dns if d not in skip]
            for fn in fns:
                found.append(os.path.relpath(os.path.join(dp, fn), ROOT).replace("\\", "/"))
        return found
    tracked = run_git("ls-files", "--cached")
    others = run_git("ls-files", "--others", "--exclude-standard")
    return sorted(set(tracked) | set(others))


def load_allow() -> list[tuple[str, str | None]]:
    f = ROOT / ".secrets-allow"
    out: list[tuple[str, str | None]] = []
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            g, _, r = line.partition("|")
            out.append((g.strip(), r.strip() or None))
    return out


def allowed(path: str, rule: str, allow: list[tuple[str, str | None]]) -> bool:
    return any(fnmatch.fnmatch(path, g) and (r is None or r == rule) for g, r in allow)


def parse_env(text: str):
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip("'\"")
        yield k, v


def collect_secret_values() -> dict[str, str]:
    """value -> 'FILE:KEY' for every secret-looking assignment in real env files on disk. Values are kept in memory only."""
    vals: dict[str, str] = {}
    skip = {".git", "node_modules", "__pycache__", "dist", ".astro"}
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in skip]
        for fn in fns:
            if not (fn == ".env" or (fn.startswith(".env.") and not fn.endswith(".example")) or fn.endswith(".env")):
                continue
            if fn.endswith(".example"):
                continue
            p = Path(dp) / fn
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            for k, v in parse_env(text):
                if len(v) >= 12 and SECRET_KEY_NAME.search(k) and not PLACEHOLDER.match(v) and not v.startswith(("http://", "https://")):
                    vals.setdefault(v, f"{rel}:{k}")
    return vals


def is_text(data: bytes) -> bool:
    return b"\x00" not in data[:4096]


def scan(mode: str, strict: bool) -> int:
    allow = load_allow()
    files = candidate_files(mode)
    secrets = collect_secret_values()
    findings: list[tuple[str, int, str, str]] = []  # path, line, rule, severity

    for rel in files:
        norm = rel.replace("\\", "/")
        if norm.endswith((".env.example",)) or norm == ".secrets-allow":
            forbidden_ok = True
        else:
            forbidden_ok = False
        if not forbidden_ok:
            for rx, why in FORBIDDEN_PATH:
                if re.search(rx, norm):
                    if not allowed(norm, "forbidden-path", allow):
                        findings.append((norm, 0, "forbidden-path: " + why, "error"))
                    break
        p = ROOT / rel
        try:
            if not p.is_file() or p.stat().st_size > MAX_BYTES:
                continue
            data = p.read_bytes()
        except OSError:
            continue
        if not is_text(data):
            continue
        text = data.decode("utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            for val, origin in secrets.items():
                if val in line and not allowed(norm, "known-secret-value", allow):
                    findings.append((norm, i, f"known-secret-value (matches {origin})", "error"))
            for rule, rx, sev in PATTERNS:
                for m in rx.finditer(line):
                    if rule == "phone-e164" and SAFE_PHONE.match(m.group(0)):
                        continue
                    if rule == "email-address":
                        dom = m.group(1).lower()
                        local = m.group(0).split("@")[0].lower()
                        if dom.endswith(SAFE_EMAIL_DOMAINS) or local in SAFE_EMAIL_LOCALS:
                            continue
                    if allowed(norm, rule, allow):
                        continue
                    findings.append((norm, i, rule, "warn" if (sev == "warn" and not strict) else "error"))
                    break

    errors = [f for f in findings if f[3] == "error"]
    warns = [f for f in findings if f[3] == "warn"]
    for path, line, rule, sev in sorted(findings):
        loc = f"{path}:{line}" if line else path
        print(f"[{sev.upper():5}] {loc}  {rule}")
    print(f"check-secrets: scanned {len(files)} files, {len(secrets)} known secret values loaded (never printed): "
          f"{len(errors)} error(s), {len(warns)} warning(s)")
    if errors:
        print("Fix: remove the value (use an env var / .env), or add a justified line to .secrets-allow. "
              "If a REAL secret was ever committed, rotate it - see docs/SECRETS.md.")
        return 1
    return 0


def main(argv: list[str]) -> int:
    mode = "default"
    strict = "--strict" in argv
    if "--staged" in argv:
        mode = "staged"
    elif "--all-files" in argv:
        mode = "all"
    return scan(mode, strict)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
