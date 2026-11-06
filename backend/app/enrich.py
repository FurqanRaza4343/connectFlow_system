"""Lead enrichment: find / crawl the company website, extract contacts with AI, and fill empty lead fields.
Ported (simplified) from legacy/orchestrator/src/enrich.mjs. All outgoing fetches are SSRF-guarded (public hosts only,
every redirect hop re-validated)."""
import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.orm import Session

from . import ai
from .config import settings
from .leadtools import normalize_phone
from .models import Lead, OutreachProfile
from .netguard import UnsafeUrl, assert_crawlable

UA = "Mozilla/5.0 (compatible; ConnectFlowBot/1.0)"
DIRECTORY = re.compile(r"indeed|mustakbil|rozee|glassdoor|zoominfo|rocketreach|crunchbase|facebook\.com|linkedin\.com|instagram\.com|twitter\.com|x\.com|youtube|wikipedia|google\.|yelp|justdial|olx", re.I)
JUNK_EMAIL = re.compile(r"@(example|test|domain|email|yourdomain|yourcompany|sample|mysite)\.[a-z.]+$|^(you|your|name|user|email|john|jane)@|sentry|wixpress|noreply|no-reply|\.(png|jpe?g|gif|svg|webp|css|js)$", re.I)


def html_to_text(html: str) -> str:
    t = re.sub(r"<(script|style|noscript|svg|template)[\s\S]*?</\1>", " ", html, flags=re.I)
    t = re.sub(r"<br\s*/?>|</(p|div|li|h\d|tr|section|article)>", "\n", t, flags=re.I)
    t = unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s*\n\s*", "\n", re.sub(r"[ \t]+", " ", t)).strip()


async def fetch_page(url: str, timeout: float = 12) -> dict | None:
    """GET with manual redirects (max 4), validating each hop. Returns {url, html, text} or None. Never raises."""
    cur = url
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers={"User-Agent": UA, "Accept": "text/html,*/*"}) as c:
            for _ in range(5):
                await assert_crawlable(cur)
                r = await c.get(cur)
                if 300 <= r.status_code < 400 and r.headers.get("location"):
                    cur = urljoin(cur, r.headers["location"])
                    continue
                if r.status_code >= 400 or not re.search(r"html|text", r.headers.get("content-type", ""), re.I):
                    return None
                html = r.text[:600_000]
                return {"url": cur, "html": html, "text": html_to_text(html)[:12_000]}
    except (UnsafeUrl, httpx.HTTPError, UnicodeDecodeError):
        return None
    return None


def clean_email(raw: str) -> str:
    e = (raw or "").replace("mailto:", "").split("?")[0].strip().lower()
    return e if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e) and not JUNK_EMAIL.search(e) else ""


async def crawl_site(website: str) -> dict:
    """Home page + up to 4 contact/about/team pages on the same host."""
    start = website if re.match(r"https?://", website, re.I) else f"https://{website}"
    home = await fetch_page(start) or (await fetch_page(start.replace("https://", "http://")) if start.startswith("https://") else None)
    if not home:
        return {"pages": [], "links": {}, "emails": [], "phones": []}
    host = urlparse(home["url"]).netloc.replace("www.", "")
    pages, seen, cands = [home], {home["url"]}, []
    links = {"linkedin": "", "facebook": "", "instagram": ""}
    emails: set[str] = set()
    phones: set[str] = set()

    def harvest(pg: dict) -> None:
        for href in re.findall(r'href\s*=\s*["\']([^"\'#]+)["\']', pg["html"], re.I):
            h = href.strip()
            if h.lower().startswith("mailto:"):
                if clean_email(h):
                    emails.add(clean_email(h))
                continue
            if h.lower().startswith("tel:"):
                phones.add(h[4:].strip())
                continue
            u = urljoin(pg["url"], h)
            for k, pat in (("linkedin", r"linkedin\.com/(company|in)/"), ("facebook", r"facebook\.com/"), ("instagram", r"instagram\.com/")):
                if not links[k] and re.search(pat, u, re.I) and not re.search(r"sharer|share\.php|login", u, re.I):
                    links[k] = u.split("?")[0]
            if urlparse(u).netloc.replace("www.", "") == host and re.search(r"contact|about|team|company|reach|leadership", u, re.I) and u not in seen:
                cands.append(u)
        for m in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", pg["text"]):
            if clean_email(m):
                emails.add(clean_email(m))

    harvest(home)
    for u in cands[:4]:
        if u in seen:
            continue
        seen.add(u)
        pg = await fetch_page(u)
        if pg:
            pages.append(pg)
            harvest(pg)
    return {"pages": [{"url": p["url"], "text": p["text"]} for p in pages], "links": links, "emails": sorted(emails)[:10], "phones": sorted(phones)[:10]}


async def searx(query: str, max_results: int = 8) -> list[dict]:
    if not settings.searxng_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as c:
            r = await c.get(f"{settings.searxng_url.rstrip('/')}/search", params={"q": query, "format": "json", "language": "en", "safesearch": "2"})
        if r.status_code != 200:
            return []
        return [{"url": x["url"], "title": x.get("title", ""), "content": x.get("content", "")} for x in r.json().get("results", [])[:max_results]]
    except (httpx.HTTPError, ValueError, KeyError):
        return []


async def fetch_site_text(website: str, max_chars: int = 3000) -> str:
    """Short readable text of a lead's website for the research prompt. Never raises."""
    if not website or settings.ai_fake:
        return ""
    m = re.search(r"https?://[^\s;(,\"']+", website) or re.search(r"[\w-]+\.[\w.-]+", website)
    if not m:
        return ""
    url = m.group(0) if m.group(0).startswith("http") else f"https://{m.group(0)}"
    pg = await fetch_page(url, timeout=10)
    return pg["text"][:max_chars] if pg else ""


async def enrich_lead(db: Session, lead: Lead, profile: OutreachProfile, overwrite: bool = False) -> dict:
    """Search + crawl + AI extraction, then fill EMPTY fields (unless overwrite). Returns the report that is stored on the lead."""
    report: dict = {"at": datetime.now(timezone.utc).isoformat(), "steps": [], "found": {}, "applied": {}}
    if settings.ai_fake:
        report["steps"].append("fake mode: no network")
        lead.enrichment, lead.enriched_at = _dump(report), datetime.now(timezone.utc)
        db.commit()
        return report

    name = re.sub(r"\b(pvt|private|ltd|limited|llc|inc|co|company)\b\.?|\(.*?\)", " ", lead.company, flags=re.I).strip()
    results: list[dict] = []
    if settings.searxng_url:
        for q in (f'"{name}" {lead.address[:60]}', f'"{name}" {lead.business_type} contact phone email', f'"{name}" official website'):
            for r in await searx(q.strip()):
                if r["url"] not in {x["url"] for x in results}:
                    results.append(r)
        report["steps"].append(f"search: {len(results)} result(s)")
    else:
        report["steps"].append("search: SearXNG not configured, skipped")

    website = lead.website
    if not website:
        email_dom = re.search(r"@([a-z0-9.-]+\.[a-z]{2,})$", (lead.email or "").lower())
        if email_dom and not re.match(r"(gmail|yahoo|hotmail|outlook|live|icloud|proton)", email_dom.group(1)):
            website = f"https://{email_dom.group(1)}"
            report["steps"].append(f"website: {email_dom.group(1)} (domain of the lead's own email)")
        else:
            cands = [r for r in results if not DIRECTORY.search(r["url"])]
            website = await ai.pick_website(name, lead.address, cands, tenant_id=lead.tenant_id) if cands else ""
            report["steps"].append(f"website: {website or 'not found'}")
    report["found"]["website"] = website

    facts: dict = {}
    if website:
        crawl = await crawl_site(website)
        report["steps"].append(f"crawl: {len(crawl['pages'])} page(s)")
        if crawl["pages"]:
            try:
                facts = await ai.extract_facts(name, crawl["pages"], tenant_id=lead.tenant_id)
            except ai.AIError as e:
                report["steps"].append(f"ai: {e}")
            facts.setdefault("emails", [])
            facts["emails"] = sorted({*(clean_email(x) for x in facts.get("emails", [])), *crawl["emails"]} - {""})[:10]
            facts.setdefault("phones", [])
            facts["phones"] = list(dict.fromkeys([*facts.get("phones", []), *crawl["phones"]]))[:10]
            for k in ("linkedin", "facebook"):
                facts[k] = facts.get(k) or crawl["links"].get(k, "")
    report["found"].update({k: facts.get(k) for k in ("summary", "emails", "phones", "linkedin", "facebook", "leadership") if facts.get(k)})

    def put(attr: str, value: str) -> None:
        value = (value or "").strip()
        if value and (overwrite or not getattr(lead, attr)):
            setattr(lead, attr, value[:300])
            report["applied"][attr] = value[:300]

    put("website", website)
    put("email", (facts.get("emails") or [""])[0])
    put("linkedin", facts.get("linkedin", ""))
    put("facebook", facts.get("facebook", ""))
    lead_people = facts.get("leadership") or []
    if lead_people and isinstance(lead_people[0], dict):
        put("contact_name", lead_people[0].get("name", ""))
    if not lead.whatsapp:  # a phone found online may give the lead a WhatsApp number
        for ph in [facts.get("whatsapp_hint", ""), *facts.get("phones", [])]:
            wa, country = normalize_phone(ph or "", profile.default_country)
            if wa:
                other = db.query(Lead.id).filter(Lead.tenant_id == lead.tenant_id, Lead.whatsapp == wa, Lead.id != lead.id).first()
                if not other:
                    lead.whatsapp, lead.country = wa, country
                    lead.phone_raw = lead.phone_raw or ph
                    if lead.status == "verify":
                        lead.status = "new"
                    report["applied"]["whatsapp"] = wa
                    break
    if facts.get("summary") and not lead.research:
        lead.research = f"{facts['summary']}"[:2000]
    lead.enrichment, lead.enriched_at = _dump(report), datetime.now(timezone.utc)
    db.commit()
    return report


def _dump(r: dict) -> str:
    import json
    return json.dumps(r)[:20000]
