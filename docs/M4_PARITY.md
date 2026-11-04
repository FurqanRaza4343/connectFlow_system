# Legacy -> ConnectFlow parity (M4 cutover checklist)

| Legacy (port 3100 / orchestrator) | ConnectFlow | Status |
|---|---|---|
| Link WhatsApp by QR | Numbers page (OpenWA gateway) | done; One Group QR scan pending (owner) |
| Leads CSV import + dedupe | Leads page + `migrate_legacy.py` | done (One Group data migrated) |
| AI research + draft per lead | Outreach engine (DeepSeek) | done |
| Approve / edit / regenerate drafts | Approvals page | done |
| Paced sending, warm-up caps | outreach_worker + warm-up `[5,10,15,20,30,40]` | done |
| STOP / opt-out handling | strict STOP (<= 60 chars) | done |
| Follow-ups | scheduler + follow-up rules | done |
| Inbox / replies | Inbox page | done |
| Appearance (accent, sidebar, page, logo) | Settings > Branding + admin theme | done |
| Knowledge file (company_knowledge.md) | Outreach setup (business profile) | done |
| Multi-number, per-number control | Numbers, per-number API keys | in progress (FORK-M4) |
| Email alerts on events | notifications + Resend | in progress (FORK-M4) |
| Multi-tenant, admin dashboard, billing requests, API keys, webhooks, team | new | new in ConnectFlow |

Not verified with real traffic: real WhatsApp sending, real site crawl, Resend to non-owner recipients, off-hours send rule.
