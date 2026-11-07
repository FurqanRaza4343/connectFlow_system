"""Media library + media in outreach (local storage backend, fake engine).

Needs AI_FAKE=true + OUTREACH_FAKE_SEND=true on the api/worker. Test workspaces are named 'MDA ...' and removed at the end.
Covers: upload validation (bytes, not names), re-encoding, limits (size, type, quota, count, rate, race), tenant isolation, variants, links,
the outreach media settings, first-message / follow-up / AI-reply attachments, safe-sending rules (young number, warm-up share, quota),
inbound media placeholders, delete rules, the public API `media` field, Redis counters (+ self-heal), indexes, orphan sweep."""
import http.cookiejar, json, os, struct, subprocess, sys, time, urllib.request, urllib.error, uuid, zlib  # noqa: E401
from concurrent.futures import ThreadPoolExecutor
from e2e_env import ADMIN_HOST, APP_HOST, BASE, INFRA, api_env, require_fake_engine

require_fake_engine()
ok_n = bad_n = 0
stamp = str(int(time.time()))
ADMIN_PW = os.environ.get("E2E_ADMIN_PASSWORD", "")
MB = 1024 * 1024


def check(name, cond, extra=""):
    global ok_n, bad_n
    cond = bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{str(extra)[:300]}]" if extra and not cond else ""))
    ok_n += cond
    bad_n += (not cond)


def sql(q):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q], cwd=INFRA, capture_output=True, text=True).stdout.strip()


def sh(service, cmd):
    return subprocess.run(["docker", "compose", "exec", "-T", service, "sh", "-c", cmd], cwd=INFRA, capture_output=True, text=True).stdout.strip()


def redis(*args):
    return subprocess.run(["docker", "compose", "exec", "-T", "redis", "redis-cli", *args], cwd=INFRA, capture_output=True, text=True).stdout.strip()


def wait(fn, timeout=40, every=1.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return fn()


# ------------------------------------------------------------------ files without any imaging library
def png(w=60, h=40, text=b"", pad=0):
    """A valid grayscale PNG (solid colour: tiny when compressed). `text` becomes a tEXt chunk (metadata that must NOT survive re-encoding)."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = (b"\x00" + bytes([random_gray()]) * w) * h
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
    if text:
        out += chunk(b"tEXt", b"Author\x00" + text)
    return out + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"") + b"\x00" * pad


def random_gray():
    return int(uuid.uuid4().int % 200) + 20


def pdf(extra=b""):
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Note (" + uuid.uuid4().hex.encode() + b") >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n" + extra


def mp4():
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + os.urandom(2048)


def jpeg_dims(data):
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in (0xC0, 0xC1, 0xC2):
            return struct.unpack(">HH", data[i + 5:i + 9])[::-1]
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


# ------------------------------------------------------------------ http client with cookies, JSON and multipart
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

    def call(self, method, path, body=None, headers=None):
        h = {"Host": self.host, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json", **(headers or {})}
        st, _, d = self._do(urllib.request.Request(BASE + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None))
        try:
            return st, (json.loads(d) if d else {})
        except ValueError:
            return st, {"raw": d[:300].decode(errors="ignore")}

    def __call__(self, method, path, body=None):
        return self.call(method, path, body)

    def raw(self, path):
        return self._do(urllib.request.Request(BASE + path, headers={"Host": self.host}))

    def upload(self, path, data, fname="file.bin", ctype="application/octet-stream", label="", tags="", field="file"):
        b = "----e2e" + uuid.uuid4().hex
        parts = []
        for k, v in (("label", label), ("tags", tags)):
            if path.endswith("/variants"):
                continue
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{field}"; filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        body = b"".join(parts)
        st, _, d = self._do(urllib.request.Request(BASE + path, method="POST", data=body, headers={"Host": self.host, "X-Requested-With": "ConnectFlow", "Content-Type": f"multipart/form-data; boundary={b}"}))
        try:
            return st, json.loads(d) if d else {}
        except ValueError:
            return st, {"raw": d[:200].decode(errors="ignore")}


def webhook(event, data):
    payload = json.dumps({"event": event, "data": data})
    code = "import hmac,hashlib,sys,httpx,os;b=sys.argv[1].encode();s='sha256='+hmac.new(os.environ['OPENWA_WEBHOOK_SECRET'].encode(),b,hashlib.sha256).hexdigest();r=httpx.post('http://localhost:8000/internal/wa-webhook',content=b,headers={'x-openwa-signature':s,'content-type':'application/json'});print(r.status_code)"
    return subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-c", code, payload], cwd=INFRA, capture_output=True, text=True).stdout.strip()


def inbound(sid, frm, body, mtype="chat", has_media=False):
    return webhook("message.received", {"sessionId": sid, "from": frm, "body": body, "type": mtype, "hasMedia": has_media, "id": str(uuid.uuid4())})


EMAILS = []
TENANTS = []


def tenant(label, plan="growth", age_days=None):
    """A verified workspace on `plan`, a ready number of that age (None = no number) and an outreach profile that sends right away."""
    email = f"mda-{label.lower()}-{stamp}@example.com"
    EMAILS.append(email)
    c = Client()
    st, me = c("POST", "/api/auth/signup", {"email": email, "password": "Sup3rSecretPw!", "company": f"MDA {label} {stamp}"})
    assert st in (200, 201), (st, me)
    sql(f"update users set email_verified_at=now() where email='{email}'")
    tid = me["tenant"]["id"]
    TENANTS.append(tid)
    sql(f"update tenants set plan_id='{plan}' where id='{tid}'")
    iid = sid = phone = None
    if age_days is not None:
        iid, sid, phone = str(uuid.uuid4()), f"sess-{label.lower()}-{stamp}", "92300" + str(uuid.uuid4().int)[:7]
        sql(f"insert into instances (id,tenant_id,label,node_id,engine,openwa_name,openwa_id,phone,status,created_at) values ('{iid}','{tid}','{label}','wa1','baileys','cf-mda-{iid[:8]}','{sid}','{phone}','ready',now() - interval '{age_days} days')")
    st, pr = c("GET", "/api/outreach/profile")
    prof = {k: v for k, v in pr.items() if k != "media"}
    prof.update({"sender_name": "Ayesha", "company_name": "MDA Co", "offer": "We test media", "batch_size": 20, "daily_limit": 100, "delay_min": 0, "delay_max": 0, "timezone": "UTC",
                 "active_from": "00:00", "active_to": "23:59", "dry_run": False, "require_approval": True, "followup_day1": 3, "followup_day2": 7})
    assert c("PUT", "/api/outreach/profile", prof)[0] == 200
    return c, tid, iid, sid, prof


def profile_put(c, prof, media=None, **over):
    body = {**prof, **over}
    if media is not None:
        body["media"] = media
    return c("PUT", "/api/outreach/profile", body)


def add_leads(c, n, tag):
    rows = ["Company,Phone,Website,Business Type"] + [f"{tag} {i},+92301{str(uuid.uuid4().int)[:7]},{tag.lower()}{i}.example,Clinic" for i in range(n)]
    st, an = c("POST", "/api/leads/import/analyze", {"csv": "\n".join(rows)})
    st, imp = c("POST", "/api/leads/import", {"csv": "\n".join(rows), "mapping": an["mapping"], "list_name": tag})
    return imp["added"]


def send_first(c, tid, n, timeout=90):
    """Draft n first messages, approve them, wait until all are sent. Returns the batch result."""
    st, r = c("POST", "/api/outreach/run-now", {"limit": n})
    ids = [d["id"] for d in c("GET", "/api/drafts?status=pending&kind=first")[1]["rows"]]
    st2, ap = c("POST", "/api/drafts/approve", {"ids": ids})
    wait(lambda: int(sql(f"select count(*) from messages where tenant_id='{tid}' and direction='out' and kind='first'") or 0) >= n, timeout, 1.5)
    return r, ap


def media_seq(tid, kind="first"):
    """media_kind of each outbound message in send order; None = plain text. (A placeholder is selected because sql() strips blank lines.)"""
    return [None if x == "text" else x for x in sql(f"select coalesce(media_kind,'text') from messages where tenant_id='{tid}' and direction='out' and kind='{kind}' order by created_at, id").splitlines()]


def keep_ready():
    """The gateway reconciler (every 5 min) marks numbers whose session it cannot find as disconnected, and these test numbers have no real session:
    put them back to 'ready' before every phase that needs a connected number."""
    sql("update instances set status='ready' where openwa_name like 'cf-mda-%'")


try:
    # ================================================================= workspaces
    A, TA, IA, SA, PA = tenant("Alpha", age_days=10)      # mature number: no media limits
    B, TB, _, _, PB = tenant("Beta")                       # isolation
    W, TW, IW, SW, PW = tenant("Warm", age_days=4)         # warming up: media share cap
    Y, TY, IY, SY, PY = tenant("Young", age_days=2)        # younger than 3 days: never media
    T, TT, _, _, _ = tenant("TrialCount", plan="trial")    # 5 items
    T2, TT2, _, _, _ = tenant("TrialBytes", plan="trial")  # 20 MB
    Q, TQ, IQ, SQ, PQ = tenant("QuotaStop", plan="trial", age_days=10)
    R, TR, _, _, _ = tenant("Rate")

    # ================================================================= upload + validation
    secret_meta = b"SecretPersonName-" + stamp.encode()
    big = png(3000, 2000, text=secret_meta)
    st, img = A.upload("/api/assets", big, "holiday.png", "image/png", "Our clinic", "Services, Portfolio")
    check("upload image -> 201 with the contract fields", st == 201 and all(k in img for k in ("id", "kind", "label", "tags", "mime", "sizeBytes", "previewUrl", "thumbUrl", "linkUrl", "usedCount", "createdAt")), (st, img))
    check("image: kind/label/tags normalised, re-encoded to JPEG, thumbnail present", img.get("kind") == "image" and img.get("label") == "Our clinic" and img.get("tags") == ["services", "portfolio"] and img.get("mime") == "image/jpeg" and img.get("thumbUrl"), img)
    st, hd, body = A.raw(img["previewUrl"])
    w, h = jpeg_dims(body) or (0, 0)
    check("served file is a JPEG of max 1600 px without the original metadata", st == 200 and body[:3] == b"\xff\xd8\xff" and max(w, h) == 1600 and secret_meta not in body, (st, w, h))
    check("file response is private + sandboxed (no public caching, nosniff)", "private" in hd.get("Cache-Control", "") and hd.get("X-Content-Type-Options") == "nosniff" and "sandbox" in hd.get("Content-Security-Policy", ""), hd)
    st, hd, th = A.raw(img["thumbUrl"])
    check("thumbnail is a small JPEG", st == 200 and th[:3] == b"\xff\xd8\xff" and max(jpeg_dims(th) or (0, 0)) <= 320, (st, len(th)))
    st, doc = A.upload("/api/assets", pdf(), "brochure.pdf", "application/pdf", "Brochure", "pricing")
    check("upload PDF -> kind document", st == 201 and doc.get("kind") == "document" and doc.get("mime") == "application/pdf" and doc.get("thumbUrl") is None, (st, doc))
    st, hd, pb = A.raw(doc["previewUrl"])
    check("PDF bytes are served unchanged", st == 200 and pb.startswith(b"%PDF-") and hd.get("Content-Type", "").startswith("application/pdf"), (st, hd.get("Content-Type")))
    st, vid = A.upload("/api/assets", mp4(), "intro.mp4", "video/mp4", "Intro video", "trust")
    check("upload MP4 -> kind video", st == 201 and vid.get("kind") == "video", (st, vid))

    exe = b"MZ\x90\x00" + os.urandom(200)
    st, r = A.upload("/api/assets", exe, "photo.png", "image/png", "evil")
    check("a Windows executable named photo.png is refused (415)", st == 415, (st, r))
    st, r = A.upload("/api/assets", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "logo.svg", "image/svg+xml", "svg")
    check("SVG with script is refused (415)", st == 415, (st, r))
    st, r = A.upload("/api/assets", pdf(b"<< /JavaScript (app.alert(1)) >>"), "x.pdf", "application/pdf", "js")
    check("PDF containing JavaScript is refused (415)", st == 415, (st, r))
    st, r = A.upload("/api/assets", pdf(), "doc.png", "image/png", "mismatch")
    check("declared type that does not match the bytes is refused (422)", st == 422, (st, r))
    st, r = A.upload("/api/assets", png(10, 10, pad=6 * MB), "huge.png", "image/png", "toobig")
    check("image over 5 MB is refused (413) with the limit in the message", st == 413 and "5 MB" in json.dumps(r), (st, r))
    st, r = A.upload("/api/assets", b"", "empty.png", "image/png", "empty")
    check("empty file is refused (422)", st == 422, (st, r))
    st, r = A.upload("/api/assets", png(7000, 7000), "pixels.png", "image/png", "bomb")
    check("image with too many pixels is refused (413)", st == 413, (st, str(r)[:150]))
    st, r = A.upload("/api/assets", os.urandom(100), "noise.bin", "application/octet-stream", "noise")
    check("unknown binary is refused (415)", st == 415, (st, r))
    st, r = B.call("POST", "/api/assets", None)
    check("upload without a file is a validation error (422)", st == 422, (st, r))
    vtok = Client()
    check("unauthenticated upload is refused", vtok.upload("/api/assets", png(), "a.png", "image/png", "x")[0] == 401)

    # ---- list / patch
    st, lst = A("GET", "/api/assets")
    ids = {i["id"] for i in lst["items"]}
    check("list shows the three items and usage numbers", st == 200 and {img["id"], doc["id"], vid["id"]} <= ids and all(k in lst["usage"] for k in ("bytes", "limitBytes", "count", "limitCount", "plan")) and lst["usage"]["count"] == 3, lst.get("usage"))
    check("rejected uploads left no half-created items behind", int(sql(f"select count(*) from assets where tenant_id='{TA}'")) == 3 and int(sql(f"select count(*) from assets where tenant_id='{TA}' and storage_key=''")) == 0)
    st, pt = A("PATCH", f"/api/assets/{doc['id']}", {"label": "Price list", "tags": ["Pricing", "Brochure"]})
    check("patch label + tags", st == 200 and pt["label"] == "Price list" and pt["tags"] == ["pricing", "brochure"], pt)
    st, lk = A("PATCH", f"/api/assets/{vid['id']}", {"tags": "trust, video"})
    check("patch accepts tags as a comma string", st == 200 and lk["tags"] == ["trust", "video"], lk)

    # ---- links
    st, link = A("POST", "/api/assets/link", {"url": "https://example.com/", "label": "Our website", "tags": ["services"]})
    check("add a link", st == 201 and link.get("kind") == "link" and link.get("linkUrl") == "https://example.com/" and link.get("previewUrl") is None and link.get("sizeBytes") == 0, (st, link))
    for bad in ("http://example.com/", "https://127.0.0.1/", "https://localhost/x", "https://10.1.2.3/", "https://user:pw@example.com/", "ftp://example.com/", "https://169.254.169.254/latest/"):
        st, r = A("POST", "/api/assets/link", {"url": bad, "label": "bad"})
        check(f"link {bad} is refused (422)", st == 422, (st, r))

    # ---- variants
    st, v1 = A.upload(f"/api/assets/{img['id']}/variants", png(80, 60, text=b"v1"), "v1.png", "image/png")
    st2, v2 = A.upload(f"/api/assets/{img['id']}/variants", png(90, 70), "v2.png", "image/png")
    check("add two variants to the image", st == 201 and st2 == 201 and len(v2.get("variants", [])) == 2, (st, st2, v2.get("variants")))
    check("a PDF cannot be a variant of an image (422)", A.upload(f"/api/assets/{img['id']}/variants", pdf(), "x.pdf", "application/pdf")[0] == 422)
    check("links have no variants (422)", A.upload(f"/api/assets/{link['id']}/variants", png(), "x.png", "image/png")[0] == 422)
    vids = [v["id"] for v in v2["variants"]]
    st, hd, vb = A.raw(f"/api/assets/{img['id']}/file?variant={vids[0]}")
    check("variant file is served", st == 200 and vb[:3] == b"\xff\xd8\xff", st)
    for i in range(3):
        A.upload(f"/api/assets/{img['id']}/variants", png(30 + i, 30), "x.png", "image/png")
    st, r = A.upload(f"/api/assets/{img['id']}/variants", png(), "x.png", "image/png")
    check("at most 5 variants per item (422)", st == 422, (st, r))
    st, r = A("DELETE", f"/api/assets/{img['id']}/variants/{vids[0]}")
    st2, lst = A("GET", "/api/assets")
    n_var = len(next(i for i in lst["items"] if i["id"] == img["id"])["variants"])
    check("delete a variant", st == 200 and n_var == 4, (st, n_var))
    # leave exactly two variants for the rotation test below
    cur = [v["id"] for v in next(i for i in lst["items"] if i["id"] == img["id"])["variants"]]
    for vid_ in cur[2:]:
        A("DELETE", f"/api/assets/{img['id']}/variants/{vid_}")
    check("two variants remain", len(next(i for i in A("GET", "/api/assets")[1]["items"] if i["id"] == img["id"])["variants"]) == 2)

    # ================================================================= tenant isolation
    st, lb = B("GET", "/api/assets")
    check("another workspace sees none of the items", st == 200 and lb["items"] == [] and lb["usage"]["count"] == 0, lb)
    check("B cannot read A's file (404)", B.raw(img["previewUrl"])[0] == 404)
    check("B cannot read A's thumbnail or variant (404)", B.raw(img["thumbUrl"])[0] == 404 and B.raw(f"/api/assets/{img['id']}/file?variant={vids[1] if len(vids) > 1 else 'x'}")[0] == 404)
    check("B cannot patch A's item (404)", B("PATCH", f"/api/assets/{img['id']}", {"label": "hacked"})[0] == 404)
    check("B cannot delete A's item (404)", B("DELETE", f"/api/assets/{img['id']}")[0] == 404)
    check("B cannot add a variant to A's item (404)", B.upload(f"/api/assets/{img['id']}/variants", png(), "x.png", "image/png")[0] == 404)
    check("B cannot delete A's variant (404)", B("DELETE", f"/api/assets/{img['id']}/variants/{cur[0]}")[0] == 404)
    st, r = profile_put(B, PB, media={"firstMessage": {"mode": "image", "assetIds": [img["id"]]}})
    check("B cannot put A's asset into its media settings (422)", st == 422, (st, r))
    check("A's label is untouched", A("GET", "/api/assets")[1]["items"][0]["label"] != "hacked")

    # ================================================================= plan limits
    pdfs = []
    for i in range(5):
        st, r = T.upload("/api/assets", pdf(), f"p{i}.pdf", "application/pdf", f"Doc {i}")
        pdfs.append((st, r))
    check("Trial: five items are allowed", all(s == 201 for s, _ in pdfs), [s for s, _ in pdfs])
    st, r = T.upload("/api/assets", pdf(), "p6.pdf", "application/pdf", "Doc 6")
    check("Trial: the 6th item -> 402 with limit/used/plan/upgradeUrl", st == 402 and r.get("limit") == 5 and r.get("used") == 5 and r.get("plan") == "trial" and r.get("upgradeUrl"), (st, r))
    st, r = T("POST", "/api/assets/link", {"url": "https://example.com/", "label": "x"})
    check("Trial: a link counts as an item (402)", st == 402, (st, r))
    # race: free one slot, then two parallel uploads -> exactly one wins
    T("DELETE", f"/api/assets/{pdfs[0][1]['id']}")
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda i: T.upload("/api/assets", pdf(), f"r{i}.pdf", "application/pdf", f"Race {i}")[0], range(2)))
    check("parallel uploads for the last slot: exactly one 201 and one 402", sorted(res) == [201, 402], res)
    check("Trial item count never exceeds 5", int(sql(f"select count(*) from assets where tenant_id='{TT}'")) == 5)

    sizes = []
    for i in range(2):
        sizes.append(T2.upload("/api/assets", pdf(b"0" * (9 * MB)), f"b{i}.pdf", "application/pdf", f"Big {i}")[0])
    st, r = T2.upload("/api/assets", pdf(b"0" * (9 * MB)), "b3.pdf", "application/pdf", "Big 3")
    check("Trial storage: 18 MB fits, the next 9 MB -> 402 'Not enough media storage'", sizes == [201, 201] and st == 402 and "storage" in json.dumps(r).lower(), (sizes, st, r))
    st, b = T2("GET", "/api/billing")
    check("billing usage exposes the media library usage and plan limits", st == 200 and b["usage"]["assets"]["count"] == 2 and b["usage"]["assets"]["limitBytes"] == 20 * MB and b["plan"]["assetCountLimit"] == 5 and b["plans"][0].get("assetBytesLimit"), b.get("usage"))

    codes = [R.upload("/api/assets", b"junk data", "e.png", "image/png", "e")[0] for _ in range(31)]
    check("upload rate limit: the 31st attempt within the hour -> 429", codes[-1] == 429 and codes[29] != 429, codes[-3:])

    # ================================================================= outreach media settings
    st, pr = A("GET", "/api/outreach/profile")
    check("profile exposes the media block with defaults", st == 200 and pr.get("media") == {"firstMessage": {"mode": "off", "assetIds": []}, "followup1": {"assetIds": []}, "followup2": {"assetIds": []}, "replies": {"enabled": False, "allowTags": []}}, pr.get("media"))
    for name, media_cfg in (("mode image without items", {"firstMessage": {"mode": "image", "assetIds": []}}),
                            ("mode image with a link", {"firstMessage": {"mode": "image", "assetIds": [link["id"]]}}),
                            ("mode link with a file", {"firstMessage": {"mode": "link", "assetIds": [img["id"]]}}),
                            ("unknown asset id", {"firstMessage": {"mode": "image", "assetIds": [str(uuid.uuid4())]}}),
                            ("too many follow-up items", {"followup1": {"assetIds": [img["id"], doc["id"], vid["id"], link["id"]]}})):
        st, r = profile_put(A, PA, media=media_cfg)
        check(f"media settings: {name} -> 422", st == 422, (st, r))
    cfg_a = {"firstMessage": {"mode": "image", "assetIds": [img["id"]]}, "followup1": {"assetIds": [img["id"], doc["id"]]}, "followup2": {"assetIds": [link["id"]]},
             "replies": {"enabled": True, "allowTags": ["Services", "portfolio"]}}
    st, r = profile_put(A, PA, media=cfg_a, followups_enabled=True)
    check("valid media settings are saved and returned normalised", st == 200 and r["media"]["firstMessage"]["mode"] == "image" and r["media"]["replies"]["allowTags"] == ["services", "portfolio"], r.get("media"))
    st, r = A("PUT", "/api/outreach/profile", {k: v for k, v in {**PA, "followups_enabled": True}.items()})
    check("a PUT without `media` keeps the saved media settings", st == 200 and r["media"]["firstMessage"]["assetIds"] == [img["id"]], r.get("media"))

    # ================================================================= first messages: mature number carries one image (variants rotate)
    add_leads(A, 8, "Alpha")
    r, ap = send_first(A, TA, 6)
    seq = media_seq(TA)
    check("mature number: all 6 first messages carried an image", seq == ["image"] * 6, seq)
    rows = sql(f"select asset_ids::text from messages where tenant_id='{TA}' and direction='out' and kind='first' order by created_at, id").splitlines()
    variants_seen = [json.loads(x)[0].get("variantId") for x in rows if x]
    check("exactly one attachment per first message, rotating original + 2 variants", all(len(json.loads(x)) == 1 for x in rows) and variants_seen[:3] == [None, vids[1] if len(vids) > 1 else None, variants_seen[2]] and len(set(variants_seen)) == 3, variants_seen)
    check("each first message is ONE WhatsApp message and counts ONCE against the plan", int(sql(f"select count(*) from messages where tenant_id='{TA}' and direction='out' and kind='first'")) == 6 and int(sql(f"select count(*) from usage_events where tenant_id='{TA}' and kind='message'")) == 6)
    st, th = A("GET", f"/api/leads/{A('GET', '/api/leads?status=contacted')[1]['rows'][0]['id']}/thread")
    m0 = th["messages"][0]
    check("thread shows the attachment of a sent message (assetId, kind, label, thumbUrl)", m0["mediaKind"] == "image" and m0["attachments"] and m0["attachments"][0]["assetId"] == img["id"] and m0["attachments"][0]["thumbUrl"], m0)
    st, inbox0 = A("GET", "/api/inbox")
    check("sent drafts keep their attachment info in the drafts list", any(d.get("attachments") is not None for d in A("GET", "/api/drafts?status=sent")[1]["rows"]))
    lead_ids = [x["id"] for x in A("GET", "/api/leads?status=contacted")[1]["rows"]]

    # ---- the draft view before sending (attachments + removing one)
    run = A("POST", "/api/outreach/run-now", {"limit": 1})
    dr = A("GET", "/api/drafts?status=pending&kind=first")[1]["rows"]
    check("a pending first draft lists its attachment (contract: assetId/kind/label/thumbUrl) and linkUrl null", dr and dr[0]["attachments"] and set(dr[0]["attachments"][0]) >= {"assetId", "kind", "label", "thumbUrl"} and dr[0]["linkUrl"] is None, dr[:1])
    d_id = dr[0]["id"]
    check("attached item cannot be deleted while a pending draft uses it (409)", A("DELETE", f"/api/assets/{img['id']}")[0] == 409)
    st, r = A("PUT", f"/api/drafts/{d_id}/attachments", {"assetIds": [img["id"], doc["id"]]})
    check("a first message accepts at most one item (422)", st == 422, (st, r))
    st, r = A("PUT", f"/api/drafts/{d_id}/attachments", {"assetIds": []})
    check("operator can remove the attachment from one draft", st == 200 and r["attachments"] == [], (st, r))
    check("B cannot change A's draft attachments (404)", B("PUT", f"/api/drafts/{d_id}/attachments", {"assetIds": []})[0] == 404)
    A("POST", f"/api/drafts/{d_id}/discard")

    # ================================================================= safe-sending rules
    PW_media = {"firstMessage": {"mode": "image", "assetIds": []}}
    st, wimg = W.upload("/api/assets", png(100, 80), "w.png", "image/png", "Warm image", "services")
    profile_put(W, PW, media={"firstMessage": {"mode": "image", "assetIds": [wimg["id"]]}})
    add_leads(W, 8, "Warm")
    send_first(W, TW, 8, 120)
    seqw = media_seq(TW)
    check("warming-up number (day 4): media only on the 4th and 7th message (max 30% of the day's sends)", seqw == [None, None, None, "image", None, None, "image", None], seqw)
    notes = sql(f"select note from drafts where tenant_id='{TW}' and kind='first' and status='sent' order by created_at, id").splitlines()
    check("text-only sends carry a plain-language reason on the draft", any("warms up" in n or "warm" in n for n in notes) and notes.count("") >= 2, notes)
    st, yimg = Y.upload("/api/assets", png(100, 80), "y.png", "image/png", "Young image", "services")
    profile_put(Y, PY, media={"firstMessage": {"mode": "image", "assetIds": [yimg["id"]]}})
    add_leads(Y, 4, "Young")
    send_first(Y, TY, 4, 90)
    seqy = media_seq(TY)
    ny = sql(f"select note from drafts where tenant_id='{TY}' and kind='first' and status='sent' limit 1")
    check("a 2-day-old number never sends media: all 4 went out as text only, with the reason", seqy == [None] * 4 and "day 3" in ny and "text only" in ny.lower(), (seqy, ny))
    check("text-only fallback still counts as normal messages", int(sql(f"select count(*) from usage_events where tenant_id='{TY}' and kind='message'")) == 4)

    # ================================================================= link mode (text + link preview, still one message)
    st, lk2 = A("POST", "/api/assets/link", {"url": "https://example.org/", "label": "Catalogue", "tags": ["services"]})
    L, TL, IL, SL, PLk = tenant("LinkMode", age_days=10)
    st, l1 = L("POST", "/api/assets/link", {"url": "https://example.com/", "label": "Our site"})
    profile_put(L, PLk, media={"firstMessage": {"mode": "link", "assetIds": [l1["id"]]}})
    add_leads(L, 1, "Linky")
    L("POST", "/api/outreach/run-now", {"limit": 1})
    ld = L("GET", "/api/drafts?status=pending&kind=first")[1]["rows"]
    check("link mode: the link is written into the draft text and linkUrl is set", ld and ld[0]["linkUrl"] == "https://example.com/" and ld[0]["body"].rstrip().endswith("https://example.com/"), ld[:1])
    L("POST", "/api/drafts/approve", {"ids": [ld[0]["id"]]})
    wait(lambda: sql(f"select count(*) from messages where tenant_id='{TL}' and direction='out'") == "1", 40, 1.5)
    check("link mode: one text message, no file", media_seq(TL) == [None] and int(sql(f"select count(*) from messages where tenant_id='{TL}' and direction='out'")) == 1)

    # ================================================================= follow-ups carry their configured assets (2 parts), quota can stop part 2
    st, qimg = Q.upload("/api/assets", png(100, 80), "q.png", "image/png", "Q image", "services")
    st, qdoc = Q.upload("/api/assets", pdf(), "q.pdf", "application/pdf", "Q doc", "pricing")
    profile_put(Q, PQ, media={"firstMessage": {"mode": "off", "assetIds": []}, "followup1": {"assetIds": [qimg["id"], qdoc["id"]]}}, followups_enabled=True)
    add_leads(Q, 1, "Quota")
    send_first(Q, TQ, 1, 60)
    qlead = sql(f"select id from leads where tenant_id='{TQ}' limit 1")
    # 48 more usage events today: the follow-up's first part is the 50th message (allowed), its second part would be the 51st (refused)
    sql(f"insert into usage_events (tenant_id, kind, at) select '{TQ}', 'message', now() from generate_series(1,48)")
    redis("del", f"q:msg:{TQ}:{time.strftime('%Y%m%d', time.gmtime())}")      # the counter heals from the database
    for tt in (TQ, TA):
        pass
    sql(f"update leads set sent_at = now() - interval '4 days' where id='{qlead}'")
    # Alpha: two contacted leads with no reply get due follow-ups too
    due_a = [i for i in lead_ids[1:3]]
    for lid in due_a:
        sql(f"update leads set sent_at = now() - interval '4 days', followup_stage = 0 where id='{lid}'")

    # AI reply on Alpha (before the follow-up scheduler touches lead 0): lead 0 asks about the business
    lead0 = lead_ids[0]
    num0 = sql(f"select whatsapp from leads where id='{lead0}'")
    check("inbound text accepted by the webhook", inbound(SA, f"{num0}@c.us", "What does your company do?") == "200")
    rep = wait(lambda: [d for d in A("GET", "/api/drafts?status=pending&kind=reply")[1]["rows"] if d["leadId"] == lead0], 30, 1)
    check("AI reply attaches ONLY allow-listed assets: the image tagged 'services', not the PDF tagged 'pricing', not the video", rep and [a["assetId"] for a in rep[0]["attachments"]] == [img["id"], link["id"]][:len(rep[0]["attachments"])] and doc["id"] not in [a["assetId"] for a in rep[0]["attachments"]] and vid["id"] not in [a["assetId"] for a in rep[0]["attachments"]] and rep[0]["attachments"], rep[:1])
    # tighten the allow-list: only 'portfolio' -> pricing/services-only assets are no longer allowed; a price question is held for a human anyway
    profile_put(A, PA, media={**cfg_a, "replies": {"enabled": True, "allowTags": ["trust"]}}, followups_enabled=True)
    num3 = sql(f"select whatsapp from leads where id='{lead_ids[3]}'")
    inbound(SA, f"{num3}@c.us", "Tell me about your services please")
    rep3 = wait(lambda: [d for d in A("GET", "/api/drafts?status=pending&kind=reply")[1]["rows"] if d["leadId"] == lead_ids[3]], 30, 1)
    check("with allow-list ['trust'] a 'services' answer attaches nothing", rep3 and rep3[0]["attachments"] == [], rep3[:1])
    profile_put(A, PA, media={**cfg_a, "replies": {"enabled": False, "allowTags": ["services"]}}, followups_enabled=True)
    num4 = sql(f"select whatsapp from leads where id='{lead_ids[4]}'")
    inbound(SA, f"{num4}@c.us", "What do you offer exactly?")
    rep4 = wait(lambda: [d for d in A("GET", "/api/drafts?status=pending&kind=reply")[1]["rows"] if d["leadId"] == lead_ids[4]], 30, 1)
    check("replies.enabled=false attaches nothing even when tags match", rep4 and rep4[0]["attachments"] == [], rep4[:1])
    profile_put(A, PA, media=cfg_a, followups_enabled=True)

    # inbound media is no longer dropped
    num5 = sql(f"select whatsapp from leads where id='{lead_ids[5]}'")
    check("inbound image accepted", inbound(SA, f"{num5}@c.us", "", "image", True) == "200")
    row = wait(lambda: sql(f"select body, media_kind from messages where tenant_id='{TA}' and direction='in' and lead_id='{lead_ids[5]}'"), 20, 1)
    check("inbound image is stored as a '[image]' placeholder with its kind", row == "[image]|image", row)
    check("inbound image notifies a human and creates no AI draft", int(sql(f"select count(*) from notifications where tenant_id='{TA}' and lead_id='{lead_ids[5]}' and type='needs_human'")) == 1 and int(sql(f"select count(*) from drafts where tenant_id='{TA}' and lead_id='{lead_ids[5]}' and kind='reply'")) == 0)
    st, ib = A("GET", "/api/inbox")
    irow = next((x for x in ib if x["leadId"] == lead_ids[5]), {})
    check("inbox row exposes mediaKind and attachments", irow.get("mediaKind") == "image" and "attachments" in irow and "linkUrl" in irow, irow)
    num6 = sql(f"select whatsapp from leads where id='{lead_ids[0]}'")
    inbound(SA, f"{num6}@c.us", "", "ptt", True)
    check("a voice note becomes '[voice]'", wait(lambda: sql(f"select count(*) from messages where tenant_id='{TA}' and direction='in' and body='[voice]' and media_kind='voice'") == "1", 20, 1))

    keep_ready()
    # wait for the follow-up scheduler (30 s tick) on Alpha and Quota
    fa = wait(lambda: (keep_ready(), [d for d in A("GET", "/api/drafts?status=pending&kind=followup")[1]["rows"]])[1], 80, 3)
    fq = wait(lambda: [d for d in Q("GET", "/api/drafts?status=pending&kind=followup")[1]["rows"]], 80, 3)
    check("Alpha follow-up drafts carry the configured items (image + PDF)", len(fa) >= 1 and [a["kind"] for a in fa[0]["attachments"]] == ["image", "document"], fa[:1])
    check("Quota follow-up draft carries two items", fq and len(fq[0]["attachments"]) == 2, fq[:1])
    ida = fa[0]["id"]
    keep_ready()
    A("POST", "/api/drafts/approve", {"ids": [ida]})
    Q("POST", "/api/drafts/approve", {"ids": [fq[0]["id"]]})
    wait(lambda: A("GET", f"/api/drafts?status=sent&kind=followup")[1]["total"] >= 1, 40, 2)
    leadf = fa[0]["leadId"]
    kinds_a = sql(f"select coalesce(media_kind,'text') from messages where tenant_id='{TA}' and lead_id='{leadf}' and kind='followup' and direction='out' order by created_at, id").splitlines()
    check("follow-up went out as 2 WhatsApp messages: image (with the text as caption) then the PDF", kinds_a == ["image", "document"], kinds_a)
    check("each part of a follow-up counted against the plan", int(sql(f"select count(*) from usage_events where tenant_id='{TA}' and kind='message'")) >= 6 + 2)
    wait(lambda: Q("GET", "/api/drafts?status=sent&kind=followup")[1]["total"] >= 1, 40, 2)
    kq = sql(f"select coalesce(media_kind,'text') from messages where tenant_id='{TQ}' and kind='followup' and direction='out'").splitlines()
    nq = sql(f"select note from drafts where tenant_id='{TQ}' and kind='followup' and status='sent'")
    check("plan quota wins: only part 1 went out, the 51st message was refused, with an explanation", kq == ["image"] and "Stopped after part 1" in nq and int(sql(f"select count(*) from usage_events where tenant_id='{TQ}' and kind='message'")) == 50, (kq, nq))

    # STOP still wins over attachments
    stop_lead = lead_ids[1]
    stopnum = sql(f"select whatsapp from leads where id='{stop_lead}'")
    inbound(SA, f"{stopnum}@c.us", "STOP")
    wait(lambda: sql(f"select status from leads where id='{stop_lead}'") == "stop", 20, 1)
    check("STOP still opts the lead out and discards its waiting drafts (attachments do not change that)", sql(f"select status from leads where id='{stop_lead}'") == "stop" and int(sql(f"select count(*) from drafts where lead_id='{stop_lead}' and status in ('pending','approved')")) == 0)

    # ================================================================= delete rules + files removed
    fcount0 = int(sh("api", f"ls /data/assets/{TA} 2>/dev/null | wc -l") or 0)
    st, r = A("DELETE", f"/api/assets/{vid['id']}")
    fcount1 = int(sh("api", f"ls /data/assets/{TA} 2>/dev/null | wc -l") or 0)
    check("deleting an unattached item removes its stored file", st == 200 and r.get("filesRemoved", 0) >= 1 and fcount1 == fcount0 - 1, (st, r, fcount0, fcount1))
    check("deleted item is gone (404 on its file)", A.raw(vid["previewUrl"])[0] == 404)
    pending_fu = [d for d in A("GET", "/api/drafts?status=pending")[1]["rows"] if any(a["assetId"] == doc["id"] for a in d["attachments"])]
    if pending_fu:
        check("an item attached to a waiting draft cannot be deleted (409)", A("DELETE", f"/api/assets/{doc['id']}")[0] == 409)
        for d in pending_fu:
            A("POST", f"/api/drafts/{d['id']}/discard")
    st, r = A("DELETE", f"/api/assets/{lk2['id']}")
    check("deleting a link works", st == 200, (st, r))

    # ================================================================= public API: media field
    keep_ready()
    st, k = A("POST", "/api/keys", {"name": "mda", "expires_in": "1d"})
    key = k.get("key", "")

    def v1(body, token=None):
        req = urllib.request.Request(BASE + "/v1/messages", method="POST", data=json.dumps(body).encode(), headers={"Host": APP_HOST, "Authorization": f"Bearer {token or key}", "Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(req, timeout=60)
            return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            d = e.read()
            try:
                return e.code, json.loads(d)
            except ValueError:
                return e.code, {}
    n0 = int(sql(f"select count(*) from usage_events where tenant_id='{TA}' and kind='message'"))
    check("/v1 text still needs `text` (422)", v1({"instance": IA, "to": "923001234567"})[0] == 422)
    check("/v1 media needs exactly one source (422)", v1({"instance": IA, "to": "923001234567", "media": {"type": "image"}})[0] == 422)
    check("/v1 media: unknown asset id -> 404", v1({"instance": IA, "to": "923001234567", "media": {"type": "image", "assetId": str(uuid.uuid4())}})[0] == 404)
    check("/v1 media: asset of another type -> 422", v1({"instance": IA, "to": "923001234567", "media": {"type": "document", "assetId": img["id"]}})[0] == 422)
    check("/v1 media: http:// url -> 422", v1({"instance": IA, "to": "923001234567", "media": {"type": "image", "url": "http://example.com/a.png"}})[0] == 422)
    check("/v1 media: private-address url -> 422", v1({"instance": IA, "to": "923001234567", "media": {"type": "image", "url": "https://127.0.0.1/a.png"}})[0] == 422)
    IB = str(uuid.uuid4())
    sql(f"insert into instances (id,tenant_id,label,node_id,engine,openwa_name,openwa_id,phone,status,created_at) values ('{IB}','{TB}','Beta','wa1','baileys','cf-mda-{IB[:8]}','sess-beta-{stamp}','923001110001','ready',now() - interval '10 days')")
    stb, kb = B("POST", "/api/keys", {"name": "mdb", "expires_in": "1d"})
    stx, rx = v1({"instance": IB, "to": "923001234567", "media": {"type": "image", "assetId": img["id"]}}, kb.get("key"))
    check("/v1 media: another workspace's asset id -> 404 'media asset not found'", stx == 404 and "asset" in json.dumps(rx).lower(), (stx, rx))
    st, r = v1({"instance": IA, "to": "923001234567", "media": {"type": "image", "assetId": img["id"], "caption": "Hi"}})
    check("/v1 media with a valid asset reaches the gateway (no real session here -> 502) and the quota use is refunded", st in (502, 429) and int(sql(f"select count(*) from usage_events where tenant_id='{TA}' and kind='message'")) == n0, (st, r))

    # ================================================================= counters, indexes, billing, capacity
    day = time.strftime("%Y%m%d", time.gmtime())
    db_n = int(sql(f"select count(*) from usage_events where tenant_id='{TA}' and kind='message' and at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc'"))
    check("Redis daily counter agrees with the database", redis("get", f"q:msg:{TA}:{day}") == str(db_n), (redis("get", f"q:msg:{TA}:{day}"), db_n))
    redis("del", f"q:msg:{TA}:{day}")
    st, b = A("GET", "/api/billing")
    check("after the counter is lost, the next read rebuilds it from the database (self-heal)", b["usage"]["messagesToday"] == db_n and redis("get", f"q:msg:{TA}:{day}") == str(db_n), (b["usage"]["messagesToday"], db_n))
    first_today = redis("hget", f"og:first:{TA}:{time.strftime('%Y-%m-%d', time.gmtime())}", IA)
    db_first = sql(f"select count(*) from messages where tenant_id='{TA}' and direction='out' and kind='first' and instance_id='{IA}'")
    check("per-number first-message counter matches the database", first_today == db_first, (first_today, db_first, redis("hgetall", f"og:first:{TA}:{time.strftime('%Y-%m-%d', time.gmtime())}"),
                                                                                           sql(f"select kind, count(*) from messages where tenant_id='{TA}' and direction='out' group by 1")))
    for nm, tid_, iid_ in (("Alpha", TA, IA), ("Warm", TW, IW), ("Young", TY, IY), ("LinkMode", TL, IL), ("QuotaStop", TQ, IQ)):
        k_ = lambda w: f"og:{w}:{tid_}:{time.strftime('%Y-%m-%d', time.gmtime())}"  # noqa: E731
        for which, cond in (("first", "and kind='first'"), ("out", ""), ("media", "and media_kind is not null")):
            got, want = redis("hget", k_(which), iid_) or "0", sql(f"select count(*) from messages where tenant_id='{tid_}' and direction='out' and instance_id='{iid_}' {cond}")
            check(f"daily counter og:{which} for {nm} equals the database", got == want, (got, want))
    idx = sql("select string_agg(indexname, ',') from pg_indexes where indexname in ('ix_messages_tenant_dir_kind_at','ix_usage_events_tenant_kind_at','ix_leads_followup')")
    check("the three new composite indexes exist", all(n in idx for n in ("ix_messages_tenant_dir_kind_at", "ix_usage_events_tenant_kind_at", "ix_leads_followup")), idx)
    plan = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc",
                           f"set enable_seqscan=off; explain select count(*) from usage_events where tenant_id='{TA}' and kind='message' and at >= now() - interval '1 day'"], cwd=INFRA, capture_output=True, text=True).stdout
    check("the per-send quota query can use ix_usage_events_tenant_kind_at", "ix_usage_events_tenant_kind_at" in plan, plan[:300])
    plan2 = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc",
                            f"set enable_seqscan=off; explain select count(*) from messages where tenant_id='{TA}' and direction='out' and kind='first' and created_at >= now() - interval '1 day'"], cwd=INFRA, capture_output=True, text=True).stdout
    check("the per-number message count is served by an index (no sequential scan)", "Index" in plan2 and "Seq Scan" not in plan2, plan2[:300])

    if ADMIN_PW:
        ad = Client(ADMIN_HOST)
        st, r = ad("POST", "/api/auth/login", {"email": os.environ.get("E2E_ADMIN_EMAIL", "admin@connectflow.example.com"), "password": ADMIN_PW})
        if st == 200:
            st, cap = ad("GET", "/api/admin/capacity")
            s = cap.get("storage", {})
            check("admin capacity reports storage figures (counts only)", st == 200 and set(s) >= {"backend", "assetsTotal", "bytesTotal", "mediaSends1h", "senderQueueDepth", "uploadFailures1h"} and s["backend"] == "local" and s["assetsTotal"] >= 1 and s["mediaSends1h"] >= 1, s)
            check("customers cannot read the admin capacity view", A("GET", "/api/admin/capacity")[0] in (401, 403, 404))
        else:
            print("SKIP  admin capacity check (admin login failed)")
    else:
        print("SKIP  admin capacity check (E2E_ADMIN_PASSWORD not set)")

    # ================================================================= orphan sweep
    old_dir = f"/data/assets/{TB}"
    orphan = f"{uuid.uuid4().hex}.jpg"
    sh("api", f"mkdir -p {old_dir} && echo x > {old_dir}/{orphan} && touch -d '3 hours ago' {old_dir}/{orphan}")
    sql(f"insert into assets (id, tenant_id, kind, label, tags, mime, size_bytes, storage_backend, storage_key, created_at) values ('{uuid.uuid4()}','{TB}','image','stale reservation','[]','image/jpeg',999,'local','', now() - interval '3 hours')")
    code = "import asyncio;from app.assets_sweep import sweep_once;from app.db import SessionLocal\nwith SessionLocal() as db: print(asyncio.run(sweep_once(db)))"
    out = subprocess.run(["docker", "compose", "exec", "-T", "worker", "python", "-c", code], cwd=INFRA, capture_output=True, text=True).stdout.strip()
    gone = sh("api", f"test -e {old_dir}/{orphan} && echo yes || echo no")
    check("sweep removes an old unreferenced file and a stale upload reservation", gone == "no" and "'orphanFiles': " in out and int(sql(f"select count(*) from assets where tenant_id='{TB}' and storage_key=''")) == 0, out)
    check("sweep keeps files that assets still refer to", sh("api", f"ls /data/assets/{TA} | wc -l") != "0")
finally:
    for t in TENANTS:
        sql(f"delete from tenants where id='{t}' and name like 'MDA %'")
    for e in EMAILS:
        sql(f"delete from users where email='{e}'")

print(f"\n{ok_n} passed, {bad_n} failed")
sys.exit(1 if bad_n else 0)
