"""WhatsApp Status: the pure parts (renderer SVG, colours, captions and links, campaign slots). No database, Redis or network."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app import status_campaigns as camp, status_engine as eng, status_render as r
from app.models import StatusPost, StatusProfile


def test_readable_ink_reaches_wcag_contrast_on_every_palette_colour():
    assert r.readable_ink("#ffffff") == "#0b1220" and r.readable_ink("#0b1b3a") == "#ffffff"
    for c in ["#004ecb", "#0f9d58", "#f97316", "#7c3aed", "#e11d48", "#d4a017", "#0b1b3a", "#f4f7fc"]:
        assert r.contrast(r.readable_ink(c), c) >= 4.5, c


def test_card_svg_escapes_text_and_has_every_design():
    assert len(r.TEMPLATES) == 8
    for t in r.TEMPLATES:
        assert "Sale &lt;b&gt; &amp; more" in r.card_svg({"template": t, "headline": "Sale <b> & more", "badge": "30% OFF", "cta": "Call", "footer": "site.com"}), t
    assert 'direction="rtl"' in r.card_svg({"headline": "آپ کا کاروبار"})


def test_card_svg_options():
    assert 'fill="#ff00aa"' in r.card_svg({"headline": "Hello", "ink": "#ff00aa"})
    assert "<text" not in r.card_svg({"headline": "Hello", "bg": "data:image/png;base64,AAAA", "plain": True})        # picture only
    assert 'opacity="0.7"' in r.card_svg({"headline": "Hi", "bg": "data:image/png;base64,AAAA", "overlay": 70})
    assert "Tap the link below" in r.card_svg({"headline": "Hi", "cta": "Go", "hint": "Tap the link below"})


def test_wrap_marks_cut_text():
    assert r.wrap("word " * 200, 80, 900, 3)[-1].endswith("…")
    assert r.wrap("", 80, 900) == []


def test_render_card_makes_a_1080x1920_png_when_the_renderer_is_installed():
    if not r.renderer_available():
        return
    png = r.render_card({"template": "badge", "headline": "Test headline", "badge": "30% OFF", "cta": "Message us", "footer": "deonegroup.com"})
    assert r.is_png(png) and int.from_bytes(png[16:20], "big") == 1080 and int.from_bytes(png[20:24], "big") == 1920


def test_resolve_link_only_allows_https_and_wa():
    p = StatusProfile(tenant_id="t", wa_number="", wa_text="Hi there")
    assert eng.resolve_link("wa", p, "92 300 1234567") == "https://wa.me/923001234567?text=Hi%20there"
    assert eng.resolve_link("wa", p, "") == ""                                        # no number anywhere: no link
    assert eng.resolve_link("https://instagram.com/x", p) == "https://instagram.com/x"
    assert eng.resolve_link("javascript:alert(1)", p) == "" and eng.resolve_link("ftp://x.y/z", p) == ""


def test_caption_is_cut_to_1024_but_the_link_survives():
    p = StatusProfile(tenant_id="t", button_text="Message us")
    post = StatusPost(caption="word " * 500, link="site.com", cta="Message us", btn_link="https://wa.me/923001234567")
    cap = eng.full_caption(post, p)
    assert len(cap) <= 1024 and "👉 Message us: https://wa.me/923001234567" in cap and "site.com" in cap


def test_clean_fields_refuses_a_bad_link_and_trims():
    try:
        eng.clean_fields({"headline": "x", "btn_link": "javascript:alert(1)"})
        raise AssertionError("expected StatusError")
    except eng.StatusError as e:
        assert e.status == 422 and "https" in str(e)
    f = eng.clean_fields({"headline": "  Hi   there ", "overlay": 999, "template": "nope", "accent": "red", "pos": "left"})
    assert f["headline"] == "Hi there" and f["overlay"] == 85 and f["template"] == "gradient" and f["accent"] == "" and f["pos"] == "center"


def test_plan_limits():
    assert eng.plan_limits("trial") == {"postsPerDay": 1, "autopilot": False, "campaigns": False}
    assert eng.plan_limits("growth")["postsPerDay"] == 5


def test_day_times():
    assert camp.day_times(interval_hours=6, window_from="09:00", window_to="22:00") == ["09:00", "15:00", "21:00"]
    assert camp.day_times(per_day=3, window_from="10:00", window_to="20:00") == ["10:00", "15:00", "20:00"]
    assert camp.day_times(interval_hours=24, window_from="11:00", window_to="21:00") == ["11:00"]
    try:
        camp.day_times(window_from="21:00", window_to="10:00")
        raise AssertionError("expected StatusError")
    except eng.StatusError:
        pass


def test_compute_slots_time_zone_numbering_and_limits():
    tz = ZoneInfo("Asia/Karachi")
    now = datetime(2026, 11, 14, 6, 0, tzinfo=timezone.utc)
    slots = camp.compute_slots(days=3, interval_hours=12, per_day=1, window_from="08:00", window_to="22:00", start=date(2026, 11, 15), tz=tz, now=now)
    assert [s["n"] for s in slots] == [1, 2, 3, 4, 5, 6] and slots[0]["time"] == "08:00" and slots[-1]["day"] == 3
    assert slots[0]["at"].startswith("2026-11-15T03:00:00")                       # 08:00 in Karachi (UTC+5) is 03:00 UTC
    # day 1 = today: slots that are already in the past are skipped
    late = camp.compute_slots(days=1, interval_hours=0, per_day=3, window_from="10:00", window_to="20:00", start=date(2026, 11, 14), tz=tz, now=datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc))
    assert [s["time"] for s in late] == ["15:00", "20:00"]
    try:
        camp.compute_slots(days=40, interval_hours=0, per_day=5, window_from="08:00", window_to="22:00", start=date(2026, 11, 15), tz=tz, now=now)
        raise AssertionError("expected StatusError")
    except eng.StatusError as e:
        assert "limit" in str(e)


def test_offers_allowed():
    assert camp.offers_allowed("30% off setup", "x") and camp.offers_allowed("", "Run a 20% discount week")
    assert not camp.offers_allowed("", "Promote our chatbot service")
