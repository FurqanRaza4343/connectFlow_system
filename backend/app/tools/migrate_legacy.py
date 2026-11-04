"""Import the legacy One Group data (Node orchestrator SQLite) into a tenant of the SaaS.

  docker compose run --rm --no-deps -v <legacy state dir>:/legacy:ro api \
      python -m app.tools.migrate_legacy --state /legacy/state.sqlite --tenant "deonegroup.com"            # dry run (default)
  ... add --apply to write.

Safe to re-run: leads are matched by WhatsApp number / company, so nothing is duplicated.
Moves: leads (+ research, first message, last reply, status, follow-up stage), lists, the conversation log (messages)
and still-open drafts (pending/approved). The WhatsApp session itself cannot be moved: relink the number by QR in the app.
"""
import argparse
import sqlite3
from datetime import datetime, timezone

from ..db import SessionLocal
from ..models import Draft, Lead, LeadList, Message, Tenant


def ts(v: str | None) -> datetime | None:
    if not v:
        return None
    v = str(v).strip().replace("T", " ").replace("Z", "")[:19]
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--tenant", required=True, help="exact workspace name")
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

        lists, id_map = {}, {}
        for r in src.execute("select * from lead_lists"):
            lists[r["id"]] = LeadList(tenant_id=t.id, name=f"{r['name']} (legacy)", source="csv", created_at=ts(r["imported_at"]) or datetime.now(timezone.utc))
        n_leads = n_skip = 0
        by_wa: dict[str, Lead] = {}
        new_leads: list[Lead] = []
        for r in src.execute("select * from leads order by id"):
            wa = (r["whatsapp"] or "").strip()
            if (wa and wa in have_wa) or (not wa and (r["company"] or "").lower() in have_co):
                n_skip += 1
                continue
            lead = Lead(
                tenant_id=t.id, company=(r["company"] or "")[:200], contact_name=(r["leadership"] or "")[:120], role=(r["role"] or "")[:200], business_type=(r["business_type"] or "")[:120],
                address=(r["address"] or "")[:300], phone_raw=(r["phone_raw"] or "")[:200], whatsapp=wa[:20], country="PK" if wa.startswith("92") else "",
                website=(r["website"] or "")[:300], email=(r["email"] or "")[:254], linkedin=(r["linkedin"] or "")[:300], facebook=(r["facebook"] or "")[:300],
                notes=(r["notes"] or "")[:4000], status=r["status"] or "new", research=r["research"] or "", first_message=r["first_message"] or "",
                sent_at=ts(r["sent_at"]), last_reply=r["last_reply"] or "", followup_stage=int(r["followup_stage"] or 0) if str(r["followup_stage"] or "0").isdigit() else 0,
                created_at=ts(r["created_at"]) or datetime.now(timezone.utc),
            )
            lead._legacy_list = r["list_id"]  # type: ignore[attr-defined]
            new_leads.append(lead)
            if wa:
                by_wa[wa] = lead
            n_leads += 1

        if a.apply:
            used = {l._legacy_list for l in new_leads}  # type: ignore[attr-defined]
            lists = {k: v for k, v in lists.items() if k in used}  # never create an empty list on a re-run
            for l in lists.values():
                db.add(l)
            db.flush()
            for lead in new_leads:
                lead.list_id = lists[lead._legacy_list].id if lead._legacy_list in lists else None  # type: ignore[attr-defined]
                db.add(lead)
            db.flush()

        n_msg = n_draft = 0
        for r in src.execute("select * from messages order by id"):
            lead = by_wa.get(r["lead_key"])
            if not lead:
                continue
            n_msg += 1
            if a.apply:
                db.add(Message(tenant_id=t.id, lead_id=lead.id, chat_id=r["chat_id"] or "", direction=r["direction"], body=r["body"], kind=r["kind"] if r["kind"] in ("first", "followup", "reply") else "first", created_at=ts(r["created_at"]) or datetime.now(timezone.utc)))
        for r in src.execute("select * from drafts where status in ('pending','approved') order by id"):
            lead = by_wa.get(r["lead_key"])
            if not lead:
                continue
            n_draft += 1
            if a.apply:
                db.add(Draft(tenant_id=t.id, lead_id=lead.id, kind=r["kind"] if r["kind"] in ("first", "followup", "reply") else "first", body=r["body"], incoming=r["incoming"] or "", status="pending", note=(r["note"] or "")[:300], source=r["source"] if r["source"] in ("ai", "manual", "fallback") else "ai", needs_human=str(r["note"] or "").startswith("NEEDS HUMAN")))

        print(f"workspace: {t.name} ({t.id})")
        print(f"leads to import: {n_leads}   already present (skipped): {n_skip}   lists: {len(lists)}   messages: {n_msg}   open drafts (as pending): {n_draft}")
        if a.apply:
            db.commit()
            print("APPLIED.")
        else:
            db.rollback()
            print("DRY RUN: nothing written. Add --apply to import.")


if __name__ == "__main__":
    main()
