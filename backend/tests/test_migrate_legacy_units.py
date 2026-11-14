"""Legacy importer: the mapping functions are pure, so they are tested without a database."""
from app.tools import migrate_legacy as m


def test_ts_parses_naive_and_iso_utc():
    assert m.ts("2026-10-28 09:30:00").isoformat() == "2026-10-28T09:30:00+00:00"
    assert m.ts("2026-10-28T09:30:00.123Z").isoformat() == "2026-10-28T09:30:00+00:00"
    assert m.ts("") is None and m.ts(None) is None and m.ts("not a date") is None


def test_region_of_reads_the_country_from_the_digits():
    assert m.region_of("923044300555") == "PK"
    assert m.region_of("971501234567") == "AE"
    assert m.region_of("") == ""
    assert m.region_of("abc") == ""


def test_lead_fields_maps_and_cleans():
    f = m.lead_fields({"company": "Acme", "leadership": "Ayesha", "whatsapp": "923044300555", "status": "contacted", "followup_stage": "2",
                       "notes": "hello", "rating": "4.5", "source": "maps", "enrichment": '{"steps":[]}', "enriched_at": "2026-10-29 10:00:00",
                       "created_at": "2026-10-28 08:00:00", "sent_at": "2026-10-28T09:00:00Z", "research": "r", "first_message": "hi", "last_reply": "ok"})
    assert f["company"] == "Acme" and f["contact_name"] == "Ayesha" and f["whatsapp"] == "923044300555" and f["country"] == "PK"
    assert f["status"] == "contacted" and f["followup_stage"] == 2
    assert f["notes"] == "hello\nGoogle rating: 4.5 · Source: maps"          # fields with no column survive as a note
    assert f["enrichment"] == '{"steps":[]}' and f["enriched_at"].year == 2026
    assert f["sent_at"].isoformat().startswith("2026-10-28T09:00:00")


def test_lead_fields_defaults_and_truncation():
    f = m.lead_fields({"company": "x" * 500, "followup_stage": "oops", "status": None, "notes": "n" * 9000})
    assert len(f["company"]) == 200 and len(f["notes"]) == 4000
    assert f["followup_stage"] == 0 and f["status"] == "new" and f["country"] == "" and f["whatsapp"] == ""
    assert f["created_at"] is not None


LEGACY = {"sender": "Abdullah", "defaultCountry": "United Arab Emirates", "timezone": "Asia/Karachi", "activeFrom": "9:05", "activeTo": "20:00",
          "dailyLimit": 15, "batchSize": 5, "intervalHours": 5, "delayMin": 90, "delayMax": 30, "requireApproval": True, "autoReply": True, "holdPricing": True,
          "fallbackTemplate": "Hi {{company}} team,\n{{sender}}", "extraRules": "Keep it short.", "paused": False, "dryRun": False,
          "adminWhatsapp": "923001112233", "targetCountries": "Pakistan,Qatar", "pageSize": 50}


def test_profile_fields_maps_settings_and_never_copies_the_run_switches():
    f, skipped = m.profile_fields(LEGACY, "# Company\nWe build AI.", "One Group", "https://deonegroup.com")
    assert f["sender_name"] == "Abdullah" and f["default_country"] == "AE" and f["timezone"] == "Asia/Karachi"
    assert f["active_from"] == "09:05" and f["active_to"] == "20:00"
    assert f["delay_min"] == 90 and f["delay_max"] == 90                      # max below min is lifted to min
    assert f["auto_reply"] is True and f["hold_pricing"] is True and f["require_approval"] is True
    assert f["fallback_template"] == "Hi {{company}} team,\n{{sender}}"      # same placeholders as the new engine
    assert f["company_name"] == "One Group" and f["website"] == "https://deonegroup.com"
    # a legacy dashboard that was LIVE (dryRun false, not paused) must NOT make the new workspace start messaging
    assert f["auto_run"] is False and f["dry_run"] is True
    assert "We build AI." in f["knowledge"] and "Keep it short." in f["knowledge"] and "Operator style rules" in f["knowledge"]
    assert set(skipped) == {"adminWhatsapp", "targetCountries", "pageSize"}


def test_profile_fields_bad_values_fall_back():
    f, _ = m.profile_fields({"defaultCountry": "Atlantis", "activeFrom": "25:00", "dailyLimit": "lots", "batchSize": 0, "intervalHours": 9999}, "", "Co", "")
    assert f["default_country"] == "PK" and f["active_from"] == "09:00" and f["daily_limit"] == 15
    assert f["batch_size"] == 1 and f["interval_hours"] == 168
    assert f["knowledge"] == ""


def test_knowledge_is_clipped_to_the_editor_limit():
    f, _ = m.profile_fields({"extraRules": "r" * 30000}, "k" * 30000, "Co", "")
    assert len(f["knowledge"]) == m.KNOWLEDGE_MAX


def test_branding_accepts_only_valid_values():
    png = "data:image/png;base64," + "A" * 100
    assert m.branding_fields({"brand_color": "#1A73E8", "brand_sidebar": "#0b1530", "brand_page": "red", "brand_logo": png}) == \
        {"brand_color": "#1a73e8", "sidebar_color": "#0b1530", "logo_data": png}
    assert m.branding_fields({"brand_logo": "data:text/html;base64,AAAA"}) == {}
    assert m.branding_fields({"brand_logo": "data:image/png;base64," + "A" * 400_000}) == {}      # over the 256 KB limit
    assert m.branding_fields({}) == {}


def test_backfill_fills_only_what_is_empty():
    legacy = m.lead_fields({"company": "A", "whatsapp": "923044300555", "rating": "4.5", "enrichment": '{"x":1}', "enriched_at": "2026-10-29 10:00:00", "notes": "keep"})
    # imported by the old tool: country guessed "PK", no enrichment, no rating note
    upd = m.backfill_fields({"country": "PK", "enrichment": "", "enriched_at": None, "notes": "my own note"}, legacy)
    assert "country" not in upd                                   # already set: never overwritten
    assert upd["enrichment"] == '{"x":1}' and upd["enriched_at"].year == 2026
    assert upd["notes"] == "my own note\nGoogle rating: 4.5"       # appended, the owner's note is kept
    assert m.backfill_fields({"country": "PK", "enrichment": '{"x":1}', "enriched_at": legacy["enriched_at"], "notes": "x\nGoogle rating: 4.5"}, legacy) == {}   # re-run changes nothing
    assert m.backfill_fields({"country": "", "enrichment": "", "enriched_at": None, "notes": ""}, legacy)["country"] == "PK"

