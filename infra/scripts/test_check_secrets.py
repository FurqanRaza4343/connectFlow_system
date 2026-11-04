"""Self-test for check-secrets.py: run with  python infra/scripts/test_check_secrets.py
Builds a throwaway git repo in a temp dir, plants fake secrets, and checks they are caught (and clean files pass)."""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).with_name("check-secrets.py")


def load(root: Path):
    spec = importlib.util.spec_from_file_location("cs", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = root
    return mod


def git(root, *a):
    subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)


def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        git(root, "init", "-q")
        (root / ".gitignore").write_text(".env\n")
        # fake "real" secret that lives only in an ignored .env
        fake = "Zq9" + "xK2mP7vLw4RtN8bYc"
        (root / ".env").write_text(f"JWT_SECRET={fake}\nADMIN_EMAIL=owner@example.com\n")
        (root / "clean.py").write_text("import os\nKEY = os.environ['JWT_SECRET']\nphone = '+15550000001'\n")
        (root / "leak1.py").write_text(f"x = '{fake}'\n")
        (root / "leak2.txt").write_text("key re_" + "AbCdEfGh_" + "x" * 24 + "\n")
        (root / "leak3.txt").write_text("call +" + "923451112233" + " now\n")
        (root / "leak4.txt").write_text("-----BEGIN RSA " + "PRIVATE KEY-----\n")
        (root / "legacy").mkdir()
        (root / "legacy" / "leads.csv").write_text("a,b\n")
        (root / "sub").mkdir()
        (root / "sub" / "prod.env").write_text("X=1\n")
        git(root, "add", "-f", "-A")
        cs = load(root)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cs.scan("staged", False)
        out = buf.getvalue()
        def expect(cond, msg):
            nonlocal ok
            print(("PASS " if cond else "FAIL ") + msg)
            ok &= cond
        expect(rc == 1, "exit code 1 when leaks exist")
        expect(fake not in out, "secret value never printed")
        expect("leak1.py:1  known-secret-value" in out, "known .env value found in code")
        expect("leak2.txt" in out and "resend-key" in out, "resend key pattern")
        expect("leak3.txt" in out and "phone-e164" in out, "phone number")
        expect("leak4.txt" in out and "private-key-block" in out, "private key block")
        expect("legacy/leads.csv" in out and "forbidden-path" in out, "legacy path forbidden")
        expect("clean.py" not in out, "clean file not flagged")
        expect("example.com" not in out, "example.com email ignored")
        # allow-list works
        (root / ".secrets-allow").write_text("leak3.txt|phone-e164\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cs.scan("staged", False)
        expect("leak3.txt" not in buf.getvalue(), ".secrets-allow suppresses a rule")
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
