"""Phone normalisation and CSV helpers for the lead-gen module (ported from the legacy Node orchestrator)."""
import csv
import io
import re

import phonenumbers

# canonical field -> header aliases (lower-case, punctuation-free)
ALIASES: dict[str, list[str]] = {
    "company": ["company", "company name", "business", "business name", "organization", "organisation", "account", "name"],
    "contact_name": ["contact", "contact name", "contact person", "person", "owner", "leadership", "first name", "full name", "decision maker"],
    "role": ["role", "role hiring for", "title", "job title", "position", "job"],
    "business_type": ["business type", "type", "category", "industry", "sector"],
    "address": ["address", "location", "city", "street address"],
    "phone": ["whatsapp number", "whatsapp", "phone", "phone raw", "mobile", "mobile number", "phone number", "contact number", "tel", "telephone", "cell"],
    "website": ["website", "url", "site", "web", "domain"],
    "email": ["email", "e mail", "email address", "mail"],
    "linkedin": ["linkedin", "linkedin url"],
    "facebook": ["facebook", "facebook url"],
    "notes": ["notes", "note", "comments", "remarks", "source"],
    "research": ["research", "research summary"],
}
FIELDS = list(ALIASES.keys())


def _key(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()


def suggest_mapping(headers: list[str]) -> dict[str, str | None]:
    """Header -> canonical field (or None). The first alias match wins; a field is mapped at most once, best alias first."""
    out: dict[str, str | None] = {h: None for h in headers}
    taken: set[str] = set()
    for field, aliases in ALIASES.items():
        for alias in aliases:  # earlier alias = stronger match
            hit = next((h for h in headers if _key(h) == alias and out[h] is None), None)
            if hit and field not in taken:
                out[hit] = field
                taken.add(field)
                break
    # a second phone-like column ("Phone (raw)" next to "WhatsApp Number") is a fallback source: the importer uses the first non-empty one
    for h in headers:
        if out[h] is None and _key(h) in ALIASES["phone"]:
            out[h] = "phone"
    return out


def parse_csv(text: str, max_rows: int = 5000) -> tuple[list[str], list[dict[str, str]]]:
    text = text.lstrip("﻿")
    if not text.strip():
        raise ValueError("The file is empty")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ValueError("The file needs a header row and at least one lead")
    headers = [h.strip() for h in rows[0]]
    if len(rows) - 1 > max_rows:
        raise ValueError(f"Too many rows ({len(rows) - 1}). The limit is {max_rows} per import.")
    body = [{headers[i]: (r[i].strip() if i < len(r) else "") for i in range(len(headers))} for r in rows[1:]]
    return headers, body


def normalize_phone(raw: str, default_region: str = "PK") -> tuple[str, str]:
    """Return (digits_e164_without_plus, ISO country) for the first valid mobile-capable number in `raw`, else ('', '').
    `raw` may hold several numbers ("+92 331 2781199 (Google); +92 313 1777563")."""
    if not raw:
        return "", ""
    chunks = [c for c in re.split(r"[;,/|\n]|\bor\b", re.sub(r"\([^)]*\)", " ", raw)) if re.search(r"\d", c)]
    for chunk in chunks:
        try:
            n = phonenumbers.parse(chunk.strip(), None if chunk.strip().startswith("+") else default_region)
        except phonenumbers.NumberParseException:
            continue
        if not phonenumbers.is_valid_number(n):
            continue
        t = phonenumbers.number_type(n)
        if t in (phonenumbers.PhoneNumberType.FIXED_LINE,):  # landlines cannot have WhatsApp
            continue
        return f"{n.country_code}{n.national_number}", phonenumbers.region_code_for_number(n) or ""
    return "", ""


def csv_safe(v: object) -> str:
    """Prevent spreadsheet formula injection in exported files."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") and not re.fullmatch(r"\+?\d[\d\s().-]*", s) else s
