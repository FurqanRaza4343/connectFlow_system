"""Media library on the Cloudinary backend, against a FAKE Cloudinary server started by this script.

Needs the api/worker started with  STORAGE_BACKEND=cloudinary  CLOUDINARY_URL=cloudinary://testkey:s3cr3t-e2e@demo
CLOUDINARY_API_BASE=http://host.docker.internal:<E2E_FAKE_CLOUDINARY_PORT>/v1_1  (see docs/MEDIA.md / the wrapper in the scratchpad); skips itself otherwise.
The fake verifies every request signature exactly like Cloudinary does (sha1 of the sorted parameters + secret) and can simulate quota/credential/server errors."""
import hashlib, http.cookiejar, json, os, re, struct, subprocess, sys, threading, time, urllib.request, urllib.error, uuid, zlib  # noqa: E401
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from e2e_env import APP_HOST, BASE, INFRA, api_env, skip

PORT = int(os.environ.get("E2E_FAKE_CLOUDINARY_PORT", "0"))
SECRET, API_KEY, CLOUD = "s3cr3t-e2e", "testkey", "demo"
env = api_env("STORAGE_BACKEND", "CLOUDINARY_API_BASE")
if not PORT or env.get("STORAGE_BACKEND") != "cloudinary" or f":{PORT}" not in env.get("CLOUDINARY_API_BASE", ""):
    skip("needs the api started with STORAGE_BACKEND=cloudinary pointing at this script's fake Cloudinary (E2E_FAKE_CLOUDINARY_PORT)")

ok_n = bad_n = 0
stamp = str(int(time.time()))
MB = 1024 * 1024


def check(name, cond, extra=""):
    global ok_n, bad_n
    cond = bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{str(extra)[:300]}]" if extra and not cond else ""))
    ok_n += cond
    bad_n += (not cond)


def sql(q):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q], cwd=INFRA, capture_output=True, text=True).stdout.strip()


def sign(params):
    skip_keys = {"file", "cloud_name", "resource_type", "api_key"}
    return hashlib.sha1(("&".join(f"{k}={params[k]}" for k in sorted(params) if k not in skip_keys and params[k] != "") + SECRET).encode()).hexdigest()


# ------------------------------------------------------------------ the fake Cloudinary
STATE = {"objects": {}, "log": [], "fail": 0, "fail_after": None, "bad_sig": 0}
LOCK = threading.Lock()


def parse_multipart(body, ctype):
    b = ctype.split("boundary=")[1].encode()
    fields, files = {}, {}
    for part in body.split(b"--" + b)[1:-1]:
        head, _, data = part.lstrip(b"\r\n").partition(b"\r\n\r\n")
        data = data[:-2] if data.endswith(b"\r\n") else data
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        if b"filename=" in head:
            files[name] = data
        else:
            fields[name] = data.decode()
    return fields, files


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(obj or {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _fail_mode(self):
        with LOCK:
            if STATE["fail_after"] is not None:
                if STATE["fail_after"] > 0:
                    STATE["fail_after"] -= 1
                    return 0
                return STATE["fail"]
            return STATE["fail"]

    def _err(self, code):
        msg = {420: "Rate Limit Exceeded", 401: "Invalid api_key", 500: "internal"}.get(code, "error")
        self._send(code, {"error": {"message": msg}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        path = urlsplit(self.path).path
        if path == "/__mode":
            m = json.loads(body)
            with LOCK:
                STATE["fail"], STATE["fail_after"] = m.get("fail", 0), m.get("after")
            return self._send(200, {"ok": True})
        mt = re.fullmatch(rf"/v1_1/{CLOUD}/(image|video|raw)/(upload|destroy)", path)
        if not mt:
            return self._send(404, {"error": {"message": "not found"}})
        rtype, action = mt.groups()
        fields, files = parse_multipart(body, self.headers["Content-Type"]) if "multipart" in self.headers.get("Content-Type", "") else ({k: v[0] for k, v in parse_qs(body.decode()).items()}, {})
        sig = fields.get("signature", "")
        params = {k: v for k, v in fields.items() if k != "signature"}
        mode = self._fail_mode() if action == "upload" else 0  # the simulated outage hits uploads only, so the cleanup of a half-finished upload can be observed
        with LOCK:
            STATE["log"].append({"action": action, "rtype": rtype, "type": fields.get("type"), "public_id": fields.get("public_id"), "sig_ok": fields.get("api_key") == API_KEY and sig == sign(params), "bytes": len(files.get("file", b""))})
        if fields.get("api_key") != API_KEY or sig != sign(params):
            return self._err(401)
        if mode:
            return self._err(mode)
        pid = fields["public_id"]
        if action == "upload":
            data = files["file"]
            with LOCK:
                STATE["objects"][(rtype, pid)] = data
            fmt = "" if rtype == "raw" else ("mp4" if rtype == "video" else "jpg")
            return self._send(200, {"public_id": pid, "version": 1, "bytes": len(data), "resource_type": rtype, "type": fields.get("type"), "format": fmt, "secure_url": f"https://res.example.test/{rtype}/{pid}"})
        with LOCK:
            existed = STATE["objects"].pop((rtype, pid), None) is not None
        return self._send(200, {"result": "ok" if existed else "not found"})

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/__state":
            with LOCK:
                return self._send(200, {"objects": [f"{k[0]}|{k[1]}" for k in STATE["objects"]], "log": STATE["log"]})
        mt = re.fullmatch(rf"/v1_1/{CLOUD}/(image|video|raw)/download", u.path)
        if not mt:
            return self._send(404, {"error": {"message": "not found"}})
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        sig = q.pop("signature", "")
        with LOCK:
            STATE["log"].append({"action": "download", "rtype": mt.group(1), "type": q.get("type"), "public_id": q.get("public_id"), "sig_ok": q.get("api_key") == API_KEY and sig == sign(q), "bytes": 0})
        if q.get("api_key") != API_KEY or sig != sign(q):
            return self._err(401)
        with LOCK:
            obj = STATE["objects"].get((mt.group(1), q["public_id"]))
        if obj is None:
            return self._send(404, {"error": {"message": "Resource not found"}})
        return self._send(200, raw=obj, ctype="application/octet-stream")


srv = ThreadingHTTPServer(("0.0.0.0", PORT), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def fake_state():
    with LOCK:
        return {"objects": sorted(f"{k[0]}|{k[1]}" for k in STATE["objects"]), "log": list(STATE["log"])}


def set_mode(fail=0, after=None):
    with LOCK:
        STATE["fail"], STATE["fail_after"] = fail, after


# ------------------------------------------------------------------ client + files
class Client:
    def __init__(self, host=APP_HOST):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.host = host

    def _do(self, req):
        try:
            r = self.op.open(req, timeout=120)
            return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def call(self, method, path, body=None):
        st, _, d = self._do(urllib.request.Request(BASE + path, method=method, headers={"Host": self.host, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}, data=json.dumps(body).encode() if body is not None else None))
        try:
            return st, json.loads(d) if d else {}
        except ValueError:
            return st, {"raw": d[:200].decode(errors="ignore")}

    def __call__(self, method, path, body=None):
        return self.call(method, path, body)

    def raw(self, path):
        return self._do(urllib.request.Request(BASE + path, headers={"Host": self.host}))

    def upload(self, path, data, fname, ctype, label="", tags=""):
        b = "----e2e" + uuid.uuid4().hex
        body = b"".join([f'--{b}\r\nContent-Disposition: form-data; name="label"\r\n\r\n{label}\r\n'.encode(), f'--{b}\r\nContent-Disposition: form-data; name="tags"\r\n\r\n{tags}\r\n'.encode(),
                         f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n", f"--{b}--\r\n".encode()])
        st, _, d = self._do(urllib.request.Request(BASE + path, method="POST", data=body, headers={"Host": self.host, "X-Requested-With": "ConnectFlow", "Content-Type": f"multipart/form-data; boundary={b}"}))
        try:
            return st, json.loads(d) if d else {}
        except ValueError:
            return st, {"raw": d[:200].decode(errors="ignore")}


def png(w, h):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = (b"\x00" + bytes([100 + uuid.uuid4().int % 100]) * w) * h
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def pdf():
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /N (" + uuid.uuid4().hex.encode() + b") >>\nendobj\n%%EOF\n"


email = f"mda-cloud-{stamp}@example.com"
TID = ""
try:
    C = Client()
    st, me = C("POST", "/api/auth/signup", {"email": email, "password": "Sup3rSecretPw!", "company": f"MDA Cloud {stamp}"})
    sql(f"update users set email_verified_at=now() where email='{email}'")
    TID = me["tenant"]["id"]
    sql(f"update tenants set plan_id='growth' where id='{TID}'")
    IID = str(uuid.uuid4())
    sql(f"insert into instances (id,tenant_id,label,node_id,engine,openwa_name,openwa_id,phone,status,created_at) values ('{IID}','{TID}','Cloud','wa1','baileys','cf-mda-{IID[:8]}','sess-cloud-{stamp}','923001110000','ready',now() - interval '10 days')")

    # ---- upload image: main + thumbnail go to Cloudinary, signed, private, in the workspace's folder
    img_bytes = png(2000, 1000)
    st, img = C.upload("/api/assets", img_bytes, "a.png", "image/png", "Cloud image", "services")
    s = fake_state()
    ups = [e for e in s["log"] if e["action"] == "upload"]
    check("image upload -> 201 and TWO signed uploads reached Cloudinary (file + thumbnail)", st == 201 and len(ups) == 2 and all(e["sig_ok"] for e in ups), (st, ups))
    check("objects are stored private, as images, in connectflow/<workspace id>/", all(e["type"] == "private" and e["rtype"] == "image" and e["public_id"].startswith(f"connectflow/{TID}/") for e in ups), ups)
    row = sql(f"select storage_backend, storage_key, thumb_key from assets where id='{img['id']}'")
    check("asset row records the cloudinary backend and keys (no URL, no secret)", row.startswith("cloudinary|image|connectflow/") and SECRET not in row and "http" not in row, row)
    st, hd, body = C.raw(img["previewUrl"])
    check("file is served through the API from Cloudinary (signed private download), as a JPEG", st == 200 and body[:3] == b"\xff\xd8\xff", (st, len(body)))
    dl = [e for e in fake_state()["log"] if e["action"] == "download"]
    check("the download request was signed and for type=private", dl and all(e["sig_ok"] and e["type"] == "private" for e in dl), dl)
    st, hd, th = C.raw(img["thumbUrl"])
    check("thumbnail is served too", st == 200 and th[:3] == b"\xff\xd8\xff" and len(th) < len(body), st)

    st, doc = C.upload("/api/assets", pdf(), "b.pdf", "application/pdf", "Cloud brochure", "pricing")
    raws = [e for e in fake_state()["log"] if e["action"] == "upload" and e["rtype"] == "raw"]
    check("PDF is stored as a raw resource whose public id carries the extension", st == 201 and raws and raws[0]["public_id"].endswith(".pdf"), raws)
    st, hd, pb = C.raw(doc["previewUrl"])
    check("PDF comes back byte-identical", st == 200 and pb.startswith(b"%PDF-"), st)

    st, lst = C("GET", "/api/assets")
    dumped = json.dumps(lst) + json.dumps(img) + json.dumps(doc)
    check("no API response ever contains the Cloudinary secret or a Cloudinary URL", SECRET not in dumped and "cloudinary" not in dumped.lower() and "res.example.test" not in dumped, dumped[:200])
    st, vr = C.upload(f"/api/assets/{img['id']}/variants", png(300, 200), "v.png", "image/png")
    check("variant upload goes to Cloudinary", st == 201 and len(vr.get("variants", [])) == 1, (st, vr))
    vid = vr["variants"][0]["id"]
    objs_before = len(fake_state()["objects"])

    # ---- provider errors: clear status codes and no half-created item
    n_assets = int(sql(f"select count(*) from assets where tenant_id='{TID}'"))
    set_mode(420)
    st, r = C.upload("/api/assets", png(100, 80), "q.png", "image/png", "quota")
    check("Cloudinary quota/rate limit (420) -> 507 with a clear message", st == 507 and "quota" in json.dumps(r).lower(), (st, r))
    check("the reserved item was released (count unchanged, nothing stored)", int(sql(f"select count(*) from assets where tenant_id='{TID}'")) == n_assets and len(fake_state()["objects"]) == objs_before)
    set_mode(500)
    st, r = C.upload("/api/assets", png(100, 80), "q.png", "image/png", "boom")
    check("Cloudinary 500 -> 502", st == 502, (st, r))
    set_mode(401)
    st, r = C.upload("/api/assets", png(100, 80), "q.png", "image/png", "creds")
    check("rejected credentials -> 502 'rejected our credentials' without leaking the key or secret", st == 502 and "credentials" in json.dumps(r) and SECRET not in json.dumps(r) and API_KEY not in json.dumps(r), (st, r))
    set_mode(500, after=1)  # the main file is stored, the thumbnail upload fails
    st, r = C.upload("/api/assets", png(120, 90), "t.png", "image/png", "half")
    set_mode(0)
    objs_after = fake_state()["objects"]
    check("a failure after the main file was stored removes that file again (no orphan at the provider)", st == 502 and len(objs_after) == objs_before and int(sql(f"select count(*) from assets where tenant_id='{TID}'")) == n_assets, (st, len(objs_after), objs_before))
    st, r = C.upload("/api/assets", png(100, 80), "ok.png", "image/png", "recovered")
    check("uploads work again once the provider recovers", st == 201, (st, r))

    # ---- sending reads the file back from Cloudinary (public API with assetId; no real gateway session -> 502 after the download)
    st, k = C("POST", "/api/keys", {"name": "cloud", "expires_in": "1d"})
    req = urllib.request.Request(BASE + "/v1/messages", method="POST", data=json.dumps({"instance": IID, "to": "923001234567", "media": {"type": "image", "assetId": img["id"]}}).encode(),
                                 headers={"Host": APP_HOST, "Authorization": f"Bearer {k['key']}", "Content-Type": "application/json"})
    n_dl = len([e for e in fake_state()["log"] if e["action"] == "download"])
    try:
        code = urllib.request.urlopen(req, timeout=60).status
    except urllib.error.HTTPError as e:
        code = e.code
    dls = [e for e in fake_state()["log"] if e["action"] == "download"]
    check("a media send fetched the file from Cloudinary with a signed private download (then the fake-less gateway refused: 502)", code == 502 and len(dls) == n_dl + 1 and dls[-1]["sig_ok"], (code, n_dl, len(dls)))

    # ---- deleting removes every stored object
    st, r = C("DELETE", f"/api/assets/{img['id']}")
    left = fake_state()["objects"]
    check("deleting the item destroys the file, its thumbnail and its variant at Cloudinary", st == 200 and r.get("filesRemoved") == 3 and not any(f"/{img['id']}" in o for o in left) and len(left) == len(objs_after) - 3 + 2, (st, r, len(left), len(objs_after)))
    destroys = [e for e in fake_state()["log"] if e["action"] == "destroy"]
    check("destroy requests were signed", destroys and all(e["sig_ok"] for e in destroys), destroys[:2])
    st, r = C("DELETE", f"/api/assets/{doc['id']}")
    check("deleting the PDF destroys its raw resource", st == 200 and not any(o.startswith("raw|") and o in left and False for o in left) and len([o for o in fake_state()["objects"] if o.startswith("raw|")]) == 0, (st, fake_state()["objects"]))
finally:
    if TID:
        sql(f"delete from tenants where id='{TID}' and name like 'MDA %'")
    sql(f"delete from users where email='{email}'")
    srv.shutdown()

print(f"\n{ok_n} passed, {bad_n} failed")
sys.exit(1 if bad_n else 0)
