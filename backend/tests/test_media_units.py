"""Unit tests for the media library: upload validation, image re-encoding, safe-sending rules, tag selection, link/part planning,
Redis daily counters (with a stub client, including self-healing), local storage, Cloudinary signing and the public-API schema.
No database, Redis or network is used."""
import asyncio
import hashlib
import io
from types import SimpleNamespace

import pytest

from app import assetlib, counters, media, outreach, storage
from app.routers import internal, v1


# ---------------------------------------------------------------- tiny valid files
def _png(w: int = 40, h: int = 30, mode: str = "RGBA") -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new(mode, (w, h), (200, 10, 10, 128) if mode == "RGBA" else (200, 10, 10)).save(b, "PNG")
    return b.getvalue()


def _jpeg_with_exif(w: int = 3000, h: int = 2000) -> bytes:
    from PIL import Image
    im = Image.new("RGB", (w, h), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "SecretPhone"       # Make
    exif[0x013B] = "Jane Doe"          # Artist
    b = io.BytesIO()
    im.save(b, "JPEG", exif=exif)
    return b.getvalue()


PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64


# ---------------------------------------------------------------- validation by bytes
@pytest.mark.parametrize("data,kind,mime", [(_png(), "image", "image/png"), (PDF, "document", "application/pdf"), (MP4, "video", "video/mp4"),
                                            (b"\xff\xd8\xff\xe0" + b"0" * 50, "image", "image/jpeg"), (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image", "image/webp"),
                                            (b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 20, "video", "video/quicktime")])
def test_detects_by_bytes(data, kind, mime):
    f = media.validate_upload(data, "")
    assert (f.kind, f.mime) == (kind, mime)


@pytest.mark.parametrize("data,status", [
    (b"MZ\x90\x00" + b"\x00" * 100, 415),                                   # Windows executable
    (b"\x7fELF\x02\x01\x01" + b"\x00" * 100, 415),                           # Linux executable
    (b"#!/bin/sh\nrm -rf /\n", 415),                                         # script
    (b"PK\x03\x04" + b"\x00" * 100, 415),                                    # zip / docx / apk
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", 415),
    (b"  <?xml version='1.0'?><svg onload='x()'/>", 415),
    (b"<!DOCTYPE html><html><script>1</script></html>", 415),
    (b"GIF89a" + b"\x00" * 30, 415),
    (b"not a known file format at all", 415),
    (b"", 422),
])
def test_rejects_dangerous_or_unknown(data, status):
    with pytest.raises(media.MediaError) as e:
        media.validate_upload(data, "")
    assert e.value.status == status


def test_declared_type_must_match_content():
    with pytest.raises(media.MediaError) as e:
        media.validate_upload(PDF, "image/png")               # a PDF pretending to be a PNG
    assert e.value.status == 422
    with pytest.raises(media.MediaError):
        media.validate_upload(_png(), "application/pdf")
    with pytest.raises(media.MediaError):
        media.validate_upload(_png(), "text/html")
    assert media.validate_upload(_png(), "image/png; charset=binary").kind == "image"   # parameters are ignored
    assert media.validate_upload(_png(), "application/octet-stream").kind == "image"    # generic upload type = "no claim"


def test_per_kind_size_limits():
    big_img = _png() + b"\x00" * (5 * media.MB)
    with pytest.raises(media.MediaError) as e:
        media.validate_upload(big_img, "")
    assert e.value.status == 413 and "5 MB" in str(e.value)
    assert media.validate_upload(PDF + b"0" * (9 * media.MB), "").kind == "document"       # 9 MB PDF is fine
    with pytest.raises(media.MediaError) as e2:
        media.validate_upload(PDF + b"0" * (11 * media.MB), "")
    assert e2.value.status == 413
    assert media.validate_upload(MP4 + b"0" * (15 * media.MB), "").kind == "video"
    with pytest.raises(media.MediaError):
        media.validate_upload(MP4 + b"0" * (17 * media.MB), "")


def test_pdf_with_scripts_is_refused():
    for bad in (b"/JavaScript", b"/JS (app.alert(1))", b"/Launch /Win", b"/EmbeddedFile"):
        with pytest.raises(media.MediaError) as e:
            media.validate_upload(PDF + b"\n<< " + bad + b" >>\n", "")
        assert e.value.status == 415
    media.validate_upload(PDF + b"/JSON not script", "")                                     # /JSON is not /JS


# ---------------------------------------------------------------- image re-encoding
def test_image_is_reencoded_resized_and_stripped():
    from PIL import Image
    src = _jpeg_with_exif()
    assert b"SecretPhone" in src                                                           # the metadata really is in the source
    jpeg, thumb, w, h = media.process_image(src)
    assert max(w, h) == media.IMAGE_MAX_SIDE and w == 1600 and abs(h - 1067) <= 1
    out = Image.open(io.BytesIO(jpeg))
    assert out.format == "JPEG" and len(out.getexif()) == 0 and b"SecretPhone" not in jpeg and b"Jane Doe" not in jpeg
    t = Image.open(io.BytesIO(thumb))
    assert max(t.size) == media.THUMB_SIDE


def test_transparent_png_becomes_flat_jpeg():
    from PIL import Image
    jpeg, _, w, h = media.process_image(_png(20, 10, "RGBA"))
    im = Image.open(io.BytesIO(jpeg))
    assert im.mode == "RGB" and (w, h) == (20, 10)


def test_corrupt_or_oversized_images_are_refused(monkeypatch):
    with pytest.raises(media.MediaError) as e:
        media.process_image(_png()[:60])                                                   # truncated
    assert e.value.status == 422
    monkeypatch.setattr(media, "MAX_PIXELS", 100)
    with pytest.raises(media.MediaError) as e2:
        media.process_image(_png(50, 50, "RGB"))
    assert e2.value.status == 413


# ---------------------------------------------------------------- tags
def test_clean_tags():
    assert media.clean_tags("Services, Portfolio ,pricing,, SERVICES") == ["services", "portfolio", "pricing"]
    assert media.clean_tags(["A b", "c!d", "x" * 80]) == ["a-b", "c-d", "x" * 30]
    assert media.clean_tags(None) == [] and media.clean_tags("") == []
    assert len(media.clean_tags([f"t{i}" for i in range(30)])) == media.MAX_TAGS


# ---------------------------------------------------------------- safe-sending rules
@pytest.mark.parametrize("age,media_today,total_today,blocked", [
    (0, 0, 0, True), (2, 0, 10, True),            # younger than 3 days: never
    (3, 0, 0, True),                              # day 3, nothing sent yet: 1 of 1 would be 100%
    (3, 0, 3, False), (3, 1, 3, True),            # 30% share: the 4th message may be media, the 5th may not (2 of 5 = 40%)
    (4, 1, 6, False), (4, 2, 6, True),
    (5, 9, 10, False),                            # warm-up is over (cap reached its maximum): no share limit
    (30, 50, 50, False),
])
def test_media_blocked_reason(age, media_today, total_today, blocked):
    r = media.media_blocked_reason(age, 5, media_today, total_today)
    assert bool(r) is blocked
    if r:
        assert "media" in r.lower()


def test_round_robin_and_selection():
    assert [media.pick_round_robin(3, i) for i in range(7)] == [0, 1, 2, 0, 1, 2, 0]
    assert media.pick_round_robin(0, 5) == 0
    pool = [{"id": "a", "tags": ["services"]}, {"id": "b", "tags": ["pricing"]}, {"id": "c", "tags": ["portfolio", "trust"]}, {"id": "d", "tags": []}]
    assert media.select_for_reply(pool, ["services", "portfolio"], ["c", "a", "b", "zzz", "a"]) == ["c", "a"]   # b not allowed, zzz unknown, a once
    assert media.select_for_reply(pool, [], ["a", "b"]) == []                                              # an empty allow-list allows nothing
    assert media.select_for_reply(pool, ["SERVICES"], ["a"]) == ["a"]                                      # case-insensitive
    assert media.select_for_reply(pool, ["services", "pricing", "portfolio"], ["a", "b", "c"], limit=2) == ["a", "b"]
    assert media.fake_pick_for_topic("services", pool) == ["a", "c"] and media.fake_pick_for_topic("pricing", pool) == ["b"] and media.fake_pick_for_topic("other", pool) == []


def test_append_links():
    assert media.append_links("Hello", ["https://x.example/a"]) == "Hello\nhttps://x.example/a"
    assert media.append_links("See https://x.example/a", ["https://x.example/a"]) == "See https://x.example/a"          # never twice
    assert media.append_links("Hi", []) == "Hi"


# ---------------------------------------------------------------- planning the WhatsApp messages of a draft
def _asset(kind: str, **kw):
    return SimpleNamespace(id=kw.pop("id", kind), kind=kind, label=kw.pop("label", kind), link_url=kw.pop("link_url", ""), link_title=kw.pop("link_title", ""), **kw)


def test_first_message_is_one_message():
    parts, note = outreach.plan_parts("Hello there", [_asset("image")], "first")
    assert [(p.kind, p.text) for p in parts] == [("image", "Hello there")] and note == ""          # image + caption
    parts, _ = outreach.plan_parts("Hello", [_asset("image", id="1"), _asset("document", id="2")], "first")
    assert len(parts) == 1                                                                           # never several bubbles
    link = _asset("link", link_url="https://x.example", link_title="X")
    parts, _ = outreach.plan_parts("Hello https://x.example", [link], "first")
    assert len(parts) == 1 and parts[0].kind == "text" and parts[0].link_preview == {"url": "https://x.example", "title": "X"}


def test_blocked_media_falls_back_to_text_with_a_reason():
    parts, note = outreach.plan_parts("Hello", [_asset("image")], "first", "this number is 1 day old")
    assert [p.kind for p in parts] == ["text"] and "text only" in note and "1 day old" in note


def test_long_text_is_not_split_for_first_messages():
    parts, note = outreach.plan_parts("x" * 2000, [_asset("image")], "first")
    assert [p.kind for p in parts] == ["text"] and "1024" in note
    parts, _ = outreach.plan_parts("x" * 2000, [_asset("image", id="1"), _asset("image", id="2")], "followup")
    assert [p.kind for p in parts] == ["text", "image", "image"]                                    # follow-ups may send text first


def test_followup_parts_caption_goes_on_the_first_file():
    parts, _ = outreach.plan_parts("Hi again", [_asset("image", id="1"), _asset("document", id="2"), _asset("video", id="3")], "followup")
    assert [(p.kind, p.text) for p in parts] == [("image", "Hi again"), ("document", ""), ("video", "")]


# ---------------------------------------------------------------- redis counters (stub client)
class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict = {}
        self.h: dict = {}

    def get(self, k):
        v = self.kv.get(k)
        return None if v is None else str(v)

    def set(self, k, v, ex=None, nx=False, keepttl=False):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    def exists(self, k):
        return int(k in self.kv or k in self.h)

    def incrby(self, k, n=1):
        self.kv[k] = int(self.kv[k]) + n
        return self.kv[k]

    def decrby(self, k, n=1):
        self.kv[k] = int(self.kv[k]) - n
        return self.kv[k]

    def hgetall(self, k):
        return {a: str(b) for a, b in self.h.get(k, {}).items()}

    def hexists(self, k, f):
        return f in self.h.get(k, {})

    def hincrby(self, k, f, n=1):
        self.h.setdefault(k, {})
        self.h[k][f] = int(self.h[k].get(f, 0)) + n

    def hsetnx(self, k, f, v):
        self.h.setdefault(k, {}).setdefault(f, v)

    def expire(self, *a, **kw):
        return True

    def pipeline(self):
        outer = self

        class P:
            def __init__(self) -> None:
                self.ops: list = []

            def hsetnx(self, *a):
                self.ops.append(a)
                return self

            def expire(self, *a):
                return self

            def execute(self):
                for a in self.ops:
                    outer.hsetnx(*a)
        return P()


class DownRedis:
    def __getattr__(self, name):
        import redis
        def boom(*a, **kw):
            raise redis.ConnectionError("down")
        return boom


def test_counter_rebuilds_from_the_database_when_missing():
    r, rows = FakeRedis(), {"n": 7}
    assert counters.get_int(r, "k", lambda: rows["n"]) == 7          # miss -> loaded once
    rows["n"] = 99
    assert counters.get_int(r, "k", lambda: rows["n"]) == 7          # hit -> the loader is not called again
    counters.incr_int(r, "k")
    assert counters.get_int(r, "k", lambda: 0) == 8


def test_increment_on_a_missing_counter_is_skipped_and_self_heals():
    """The event row is committed BEFORE the increment. If the counter was lost, skipping the increment and rebuilding from the DB is exact."""
    r, db_rows = FakeRedis(), 5
    counters.incr_int(r, "k")                                        # key absent: nothing happens (no phantom '1')
    assert "k" not in r.kv
    assert counters.get_int(r, "k", lambda: db_rows) == 5            # rebuilt from the database, which already includes the new row


def test_decrement_never_goes_below_zero():
    r = FakeRedis()
    counters.get_int(r, "k", lambda: 0)
    counters.decr_int(r, "k")
    assert counters.get_int(r, "k", lambda: 9) == 0


def test_hash_counts_rebuild_increment_and_heal():
    r = FakeRedis()
    db = {"i1": 2, "-": 1}
    assert counters.hash_counts(r, "h", lambda: dict(db)) == {"i1": 2, "-": 1}
    counters.hash_incr(r, "h", "i1")
    counters.hash_incr(r, "h", "i2")
    assert counters.hash_counts(r, "h", lambda: {}) == {"i1": 3, "-": 1, "i2": 1}
    r.h.clear()                                                      # Redis restarted: everything is gone
    counters.hash_incr(r, "h", "i1")                                 # skipped, no sentinel
    assert counters.hash_counts(r, "h", lambda: {"i1": 4}) == {"i1": 4}   # healed from the DB (which includes that message)


def test_counters_fall_back_to_the_database_when_redis_is_down():
    d = DownRedis()
    assert counters.get_int(d, "k", lambda: 12) == 12
    assert counters.hash_counts(d, "h", lambda: {"a": 3}) == {"a": 3}
    counters.incr_int(d, "k")
    counters.decr_int(d, "k")
    counters.hash_incr(d, "h", "a")                                  # none of these raise


# ---------------------------------------------------------------- local storage
def test_local_storage_roundtrip_delete_and_sweep(tmp_path):
    async def run():
        st = storage.LocalStorage(str(tmp_path))
        tid = "11111111-2222-3333-4444-555555555555"
        key = await st.put(tid, b"hello bytes", "pdf", "document")
        assert key.startswith(tid + "/") and key.endswith(".pdf")
        assert await st.get(key) == b"hello bytes"
        assert b"".join([c async for c in st.stream(key)]) == b"hello bytes"
        other = await st.put(tid, b"orphan", "jpg", "image")
        import os, time
        old = time.time() - 7200
        os.utime(tmp_path / other, (old, old))
        assert await st.sweep_orphans({key}, min_age_seconds=3600) == 1      # the unreferenced old file goes, the known one stays
        assert (tmp_path / key).exists() and not (tmp_path / other).exists()
        await st.delete(key)
        with pytest.raises(storage.StorageError) as e:
            await st.get(key)
        assert e.value.status == 404
    asyncio.run(run())


@pytest.mark.parametrize("key", ["../../etc/passwd", "11111111-2222-3333-4444-555555555555/../x.pdf", "abc", "11111111-2222-3333-4444-555555555555/ZZZ.pdf", "/abs/path.pdf"])
def test_local_storage_rejects_bad_keys(tmp_path, key):
    st = storage.LocalStorage(str(tmp_path))
    with pytest.raises(storage.StorageError):
        asyncio.run(st.get(key))


# ---------------------------------------------------------------- cloudinary
def test_cloudinary_url_and_signature():
    assert storage.parse_cloudinary_url("cloudinary://123:s3cr3t@demo") == ("demo", "123", "s3cr3t")
    for bad in ("", "https://x", "cloudinary://onlyhost", "cloudinary://k@cloud"):
        with pytest.raises(storage.StorageError):
            storage.parse_cloudinary_url(bad)
    params = {"timestamp": "1315060510", "public_id": "sample", "file": "x", "api_key": "k", "resource_type": "image", "type": ""}
    # sorted k=v pairs, without file/api_key/resource_type/cloud_name and without empty values, then the secret appended: sha1
    assert storage.cloudinary_signature(params, "abcd") == hashlib.sha1(b"public_id=sample&timestamp=1315060510abcd").hexdigest()


def test_cloudinary_key_roundtrip_and_error_mapping():
    c = storage.CloudinaryStorage("cloudinary://k:s@demo", "https://api.example.test/v1_1")
    assert c._split("raw|connectflow/t/x.pdf|") == ("raw", "connectflow/t/x.pdf", "")
    assert c._split("image|connectflow/t/abc|jpg") == ("image", "connectflow/t/abc", "jpg")
    import httpx
    quota = httpx.Response(420, json={"error": {"message": "Rate Limit Exceeded"}})
    assert c._fail(quota).status == 507
    assert c._fail(httpx.Response(400, json={"error": {"message": "Storage quota exceeded"}})).status == 507
    assert c._fail(httpx.Response(401, json={"error": {"message": "Invalid api_key"}})).status == 502
    assert c._fail(httpx.Response(500, text="boom")).status == 502
    assert "secret" not in str(c._fail(httpx.Response(401, json={"error": {"message": "Invalid api_key k"}})).args)


# ---------------------------------------------------------------- outreach media settings / API schemas / inbound kinds
def test_normalize_media_fills_defaults_and_cleans():
    assert assetlib.normalize_media({}) == assetlib.DEFAULT_MEDIA
    assert assetlib.normalize_media(None) == assetlib.DEFAULT_MEDIA
    got = assetlib.normalize_media({"firstMessage": {"mode": "bogus", "assetIds": ["a", 5]}, "replies": {"enabled": 1, "allowTags": ["Services", "x y"]}, "followup1": "nope"})
    assert got["firstMessage"] == {"mode": "off", "assetIds": ["a"]} and got["followup1"] == {"assetIds": []}
    assert got["replies"] == {"enabled": True, "allowTags": ["services", "x-y"]}


def test_public_api_media_schema():
    ok = v1.MessageIn(instance="i", to="923001234567", media={"type": "image", "assetId": "a1", "caption": "hi"})
    assert ok.media is not None and ok.text == ""
    assert v1.MessageIn(instance="i", to="1", text="plain").media is None
    for bad in ({"instance": "i", "to": "1"},                                                                       # neither text nor media
                {"instance": "i", "to": "1", "text": "  "},
                {"instance": "i", "to": "1", "media": {"type": "image"}},                                          # no source
                {"instance": "i", "to": "1", "media": {"type": "image", "assetId": "a", "url": "https://x.example/a.png"}},   # two sources
                {"instance": "i", "to": "1", "media": {"type": "gif", "assetId": "a"}}):
        with pytest.raises(ValueError):
            v1.MessageIn(**bad)


@pytest.mark.parametrize("t,has,kind", [("image", False, "image"), ("ptt", False, "voice"), ("audio", False, "voice"), ("document", False, "document"), ("sticker", False, "sticker"),
                                        ("chat", False, None), ("", False, None), ("weird", True, "media")])
def test_inbound_media_kind(t, has, kind):
    assert internal.media_kind(t, has) == kind


def test_config_rejects_unknown_storage_backend(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("STORAGE_BACKEND", "dropbox")
    with pytest.raises(ValueError):
        Settings()  # type: ignore[call-arg]
    monkeypatch.setenv("STORAGE_BACKEND", "cloudinary")
    monkeypatch.setenv("CLOUDINARY_URL", "")
    with pytest.raises(ValueError):
        Settings()  # type: ignore[call-arg]
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://k:s@demo")
    assert Settings().storage_backend == "cloudinary"  # type: ignore[call-arg]
