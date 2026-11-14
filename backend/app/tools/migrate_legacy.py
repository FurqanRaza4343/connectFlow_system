"""Import the legacy One Group data (Node orchestrator SQLite) into a tenant of the SaaS.

  docker compose run --rm --no-deps -v <legacy dir>:/legacy:ro api \
      python -m app.tools.migrate_legacy --state /legacy/orchestrator/state/state.sqlite \
      --knowledge /legacy/data/company_knowledge.md --tenant "deonegroup.com"            # dry run (default)
  ... add --apply to write.

What moves
  * leads (+ research, first message, last reply, status, follow-up stage, enrichment, rating/source as a note), lists,
    the conversation log (messages) and still-open drafts (pending / approved);
  * the outreach SETTINGS (sender, country, hours, limits, delays, templates ...) into the workspace's outreach profile;
  * the company knowledge document (+ the operator's extra style rules) into the profile's `knowledge`;
  * branding (accent / sidebar / page colours, logo) into the workspace, only where the workspace has none yet.
What does NOT move (printed at the end so nothing is silently lost)
  * the WhatsApp session itself: relink the number by QR in the app;
  * the admin-WhatsApp notification numbers and a few legacy-only settings (no home in the new profile);
  * notifications, batches history, sent / discarded drafts (sent ones are already in the conversation log), the Status test posts.
Safety: the imported workspace starts with `auto_run = false` and `dry_run = true` whatever the legacy switches were, so the new
system never starts messaging real people by itself; turn them on in Outreach settings when you are ready.
Safe to re-run: leads are matched by WhatsApp number / company, so nothing is duplicated (messages and drafts only follow new leads).
"""
import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import SessionLocal
from ..models import Draft, Lead, LeadList, Message, OutreachProfile, Tenant

# legacy country NAME (Settings -> Default country) -> ISO code used by the new profile
COUNTRY_CODES = {
    "Pakistan": "PK", "United Arab Emirates": "AE", "Saudi Arabia": "SA", "Qatar": "QA", "Kuwait": "KW", "Bahrain": "BH", "Oman": "OM",
    "United Kingdom": "GB", "United States": "US", "Canada": "CA", "Australia": "AU", "New Zealand": "NZ", "Singapore": "SG", "Malaysia": "MY", "India": "IN",
}
KNOWLEDGE_MAX = 20000          # the profile editor allows 20 000 characters
LEGACY_ONLY_SETTINGS = ("adminWhatsapp", "notifyBatchWhatsapp", "notifyReplyWhatsapp", "targetCountries", "pageSize", "enrichOverwrite", "countryPrefixes")


def ts(v: str | None) -> datetime | None:
    if not v:
        return None
    v = str(v).strip().replace("T", " ").replace("Z", "")[:19]
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def region_of(wa: str) -> str:
    """ISO region of an international number written as digits (923001234567 -> PK); '' when it cannot be told."""
    if not wa:
        return ""
    try:
        import phonenumbers
        return phonenumbers.region_code_for_number(phonenumbers.parse("+" + wa, None)) or ""
    except Exception:  # noqa: BLE001
        return ""


def hhmm(v: Any, default: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(v or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m and int(m.group(1)) < 24 and int(m.group(2)) < 60 else default


def as_int(v: Any, default: int, lo: int = 0, hi: int = 100000) -> int:
    try:
        return min(hi, max(lo, int(float(v))))
    except (TypeError, ValueError):
        return default


def lead_fields(r: dict[str, Any]) -> dict[str, Any]:
    """Legacy `leads` row -> kwargs of the new Lead (pure function: unit-tested)."""
    wa = (r.get("whatsapp") or "").strip()[:20]
    notes = (r.get("notes") or "").strip()
    extra = [f"{k}: {r[col]}" for k, col in (("Google rating", "rating"), ("Source", "source")) if str(r.get(col) or "").strip()]
    if extra:
        notes = (notes + "\n" if notes else "") + " · ".join(extra)
    stage = str(r.get("followup_stage") or "0")
    status = r.get("status") or "new"
    return dict(
        company=(r.get("company") or "")[:200], contact_name=(r.get("leadership") or "")[:120], role=(r.get("role") or "")[:200],
        business_type=(r.get("business_type") or "")[:120], address=(r.get("address") or "")[:300], phone_raw=(r.get("phone_raw") or "")[:200],
        whatsapp=wa, country=region_of(wa), website=(r.get("website") or "")[:300], email=(r.get("email") or "")[:254],
        linkedin=(r.get("linkedin") or "")[:300], facebook=(r.get("facebook") or "")[:300], notes=notes[:4000], status=status,
        research=r.get("research") or "", first_message=r.get("first_message") or "", sent_at=ts(r.get("sent_at")), last_reply=r.get("last_reply") or "",
        followup_stage=int(stage) if stage.isdigit() else 0, enrichment=r.get("enrichment") or "", enriched_at=ts(r.get("enriched_at")),
        created_at=ts(r.get("created_at")) or datetime.now(timezone.utc),
    )


def backfill_fields(existing: dict[str, Any], legacy: dict[str, Any]) -> dict[str, Any]:
    """Updates for a lead that was imported by an OLDER version of this tool: fills only what is still empty (pure function)."""
    out: dict[str, Any] = {}
    for k in ("country", "enrichment", "enriched_at"):
        if not existing.get(k) and legacy.get(k):
            out[k] = legacy[k]
    notes = existing.get("notes") or ""
    for line in (legacy.get("notes") or "").split("\n"):
        if line.startswith("Google rating:") and line not in notes:
            notes = (notes + "\n" if notes else "") + line
    if notes != (existing.get("notes") or ""):
        out["notes"] = notes[:4000]
    return out


def profile_fields(s: dict[str, Any], knowledge: str, company_name: str, website: str) -> tuple[dict[str, Any], list[str]]:
    """Legacy settings -> OutreachProfile kwargs, plus the legacy-only keys that have no home (pure function: unit-tested).
    The RUN switches are never copied: the new workspace starts with auto_run=false and dry_run=true."""
    know = (knowledge or "").strip()
    rules = str(s.get("extraRules") or "").strip()
    if rules:
        know = (know + "\n\n" if know else "") + "## Operator style rules (from the old dashboard)\n" + rules
    out: dict[str, Any] = {
        "sender_name": str(s.get("sender") or "")[:80], "default_country": COUNTRY_CODES.get(str(s.get("defaultCountry") or ""), "PK"),
        "timezone": str(s.get("timezone") or "UTC")[:40], "active_from": hhmm(s.get("activeFrom"), "09:00"), "active_to": hhmm(s.get("activeTo"), "20:00"),
        "daily_limit": as_int(s.get("dailyLimit"), 15, 0, 1000), "batch_size": as_int(s.get("batchSize"), 5, 1, 100), "interval_hours": as_int(s.get("intervalHours"), 5, 1, 168),
        "delay_min": as_int(s.get("delayMin"), 60, 0, 3600), "delay_max": as_int(s.get("delayMax"), 180, 0, 7200),
        "require_approval": bool(s.get("requireApproval", True)), "auto_reply": bool(s.get("autoReply", False)), "hold_pricing": bool(s.get("holdPricing", True)),
        "fallback_template": str(s.get("fallbackTemplate") or "")[:3000], "knowledge": know[:KNOWLEDGE_MAX],
        "company_name": company_name[:120], "website": website[:300],
        "auto_run": False, "dry_run": True,
    }
    if out["delay_max"] < out["delay_min"]:
        out["delay_max"] = out["delay_min"]
    skipped = [k for k in LEGACY_ONLY_SETTINGS if k in s and s.get(k) not in ("", None, False)]
    return out, skipped


def branding_fields(meta: dict[str, str]) -> dict[str, str]:
    """Legacy `meta` branding -> Tenant columns (only valid values: #rrggbb colours, an image data URL of at most 256 KB)."""
    out: dict[str, str] = {}
    for src, dst in (("brand_color", "brand_color"), ("brand_sidebar", "sidebar_color"), ("brand_page", "page_color")):
        if re.fullmatch(r"#[0-9a-fA-F]{6}", meta.get(src) or ""):
            out[dst] = meta[src].lower()
    logo = meta.get("brand_logo") or ""
    if re.fullmatch(r"data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+", logo) and len(logo) <= 350_000:
        out["logo_data"] = logo
    return out


def _json(v: str | None) -> Any:
    try:
        return json.loads(v) if v is not None else None
    except ValueError:
        return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True, help="path of the legacy state.sqlite")
    ap.add_argument("--tenant", required=True, help="exact workspace name")
    ap.add_argument("--knowledge", default="", help="path of company_knowledge.md (optional)")
    ap.add_argument("--company-name", default="", help="company name used in messages (default: the profile's, else the workspace name)")
    ap.add_argument("--website", default="", help="company website for the profile (optional)")
    ap.add_argument("--no-settings", action="store_true", help="import leads and messages only")
    ap.add_argument("--backfill", action="store_true", help="also fill empty country / enrichment / rating notes of leads imported by an older version")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    src = sqlite3.connect(f"file:{a.state}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    with SessionLocal() as db:
        t = db.query(Tenant).filter(Tenant.name == a.tenant).first()
        if not t:
            raise SystemExit(f"No workspace named {a.tenant!r}. Existing: {[x.name for x in db.query(Tenant).all()]}")
        have_wa = {w for (w,) in db.query(Lead.whatsapp).filter(Lead.tenant_id == t.id, Lead.whatsapp != "").all()}
        have_co = {c.lower() for (c,) in db.query(Lead.company).filter(Lead.tenant_id == t.id).all()}
        existing_wa = {l.whatsapp: l for l in db.query(Lead).filter(Lead.tenant_id == t.id, Lead.whatsapp != "").all()} if a.backfill else {}
        existing_co = {l.company.lower(): l for l in db.query(Lead).filter(Lead.tenant_id == t.id, Lead.whatsapp == "").all()} if a.backfill else {}
        n_backfilled = 0

        lists: dict[Any, Any] = {}
        for r in src.execute("select * from lead_lists"):
            lists[r["id"]] = LeadList(tenant_id=t.id, name=f"{r['name']} (legacy)", source="csv", created_at=ts(r["imported_at"]) or datetime.now(timezone.utc))
        n_skip = 0
        by_wa: dict[str, Lead] = {}
        new_leads: list[Lead] = []
        for r in src.execute("select * from leads order by id"):
            row = dict(r)
            wa = (row.get("whatsapp") or "").strip()
            co = (row.get("company") or "").lower()
            if (wa and wa in have_wa) or (not wa and co in have_co):
                n_skip += 1
                old = existing_wa.get(wa) if wa else existing_co.get(co)
                if a.backfill and old is not None:
                    upd = backfill_fields({k: getattr(old, k) for k in ("country", "enrichment", "enriched_at", "notes")}, lead_fields(row))
                    if upd:
                        n_backfilled += 1
                        if a.apply:
                            for k, v in upd.items():
                                setattr(old, k, v)
                continue
            lead = Lead(tenant_id=t.id, **lead_fields(row))
            lead._legacy_list = row.get("list_id")  # type: ignore[attr-defined]
            new_leads.append(lead)
            if wa:
                by_wa[wa] = lead
                have_wa.add(wa)          # the same number twice in the file must not hit the unique index
            else:
                have_co.add(co)
        n_leads = len(new_leads)
        used = {l._legacy_list for l in new_leads}  # type: ignore[attr-defined]
        lists = {k: v for k, v in lists.items() if k in used}  # never create an empty list on a re-run

        if a.apply:
            for l in lists.values():
                db.add(l)
            db.flush()
            for lead in new_leads:
                lead.list_id = lists[lead._legacy_list].id if lead._legacy_list in lists else None  # type: ignore[attr-defined]
                db.add(lead)
            db.flush()

        n_msg = n_draft = 0
        with_draft: set[str] = set()
        for r in src.execute("select * from messages order by id"):
            found = by_wa.get(r["lead_key"])
            if not found:
                continue
            n_msg += 1
            if a.apply:
                db.add(Message(tenant_id=t.id, lead_id=found.id, chat_id=r["chat_id"] or "", direction=r["direction"], body=r["body"], kind=r["kind"] if r["kind"] in ("first", "followup", "reply") else "first", created_at=ts(r["created_at"]) or datetime.now(timezone.utc)))
        for r in src.execute("select * from drafts where status in ('pending','approved') order by id"):
            found = by_wa.get(r["lead_key"])
            if not found:
                continue
            n_draft += 1
            with_draft.add(r["lead_key"])
            if a.apply:
                db.add(Draft(tenant_id=t.id, lead_id=found.id, kind=r["kind"] if r["kind"] in ("first", "followup", "reply") else "first", body=r["body"], incoming=r["incoming"] or "", status="pending", note=(r["note"] or "")[:300], source=r["source"] if r["source"] in ("ai", "manual", "fallback") else "ai", needs_human=str(r["note"] or "").startswith("NEEDS HUMAN")))
        for wa, lead in by_wa.items():   # 'queued' without a draft would never be picked up again
            if lead.status == "queued" and wa not in with_draft:
                lead.status = "new"

        # ---- settings, knowledge, branding
        changed: list[str] = []
        skipped_settings: list[str] = []
        if not a.no_settings:
            s_rows = {r["key"]: _json(r["value"]) for r in src.execute("select key, value from settings")}
            meta = {r["key"]: r["value"] for r in src.execute("select key, value from meta")}
            knowledge = Path(a.knowledge).read_text(encoding="utf-8") if a.knowledge and Path(a.knowledge).exists() else ""
            prof = db.get(OutreachProfile, t.id)
            company = a.company_name or (prof.company_name if prof and prof.company_name else t.name)
            site = a.website or (prof.website if prof and prof.website else t.website_url or "")
            fields, skipped_settings = profile_fields(s_rows, knowledge, company, site)
            if prof is None:
                prof = OutreachProfile(tenant_id=t.id)
                if a.apply:
                    db.add(prof)
            for k, v in fields.items():
                if getattr(prof, k, None) != v:
                    changed.append(k)
                if a.apply:
                    setattr(prof, k, v)
            for k, v in branding_fields(meta).items():
                if getattr(t, k) in (None, ""):      # never overwrite a look the workspace already chose
                    changed.append(f"tenant.{k}")
                    if a.apply:
                        setattr(t, k, v)

        print(f"workspace: {t.name} ({t.id})")
        print(f"leads to import: {n_leads}   already present (skipped): {n_skip}   lists: {len(lists)}   messages: {n_msg}   open drafts (as pending): {n_draft}")
        if a.backfill:
            print(f"older leads with empty fields filled in (country / enrichment / rating note): {n_backfilled}")
        if not a.no_settings:
            print(f"profile/branding fields set: {len(changed)}  ({', '.join(changed) if changed else 'nothing new'})")
            print("run switches: auto_run=false, dry_run=true (the new workspace never starts messaging by itself)")
            if skipped_settings:
                print("NOT migrated (no home in the new profile): " + ", ".join(skipped_settings) + " (adminWhatsapp etc.: set notifications in Settings)")
        print("NOT migrated: WhatsApp session (relink by QR), notifications, batches history, sent/discarded drafts, Status test posts")
        if a.apply:
            db.commit()
            print("APPLIED.")
        else:
            db.rollback()
            print("DRY RUN: nothing written. Add --apply to import.")


if __name__ == "__main__":
    main()
