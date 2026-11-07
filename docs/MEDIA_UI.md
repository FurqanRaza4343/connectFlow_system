# Media library UI

Frontend for the Assets library: where customers keep the pictures, PDFs, videos and links their messages can carry, and where they choose what goes with first messages, follow-ups and AI replies. Plan: `C:\Users\…\.claude\plans\hey-claude-just-hum-structured-sunset.md` (v5). Backend contract: `backend/app/routers/assets.py`.

## Screens

| Screen | Where | Who |
|---|---|---|
| **Assets** | Outreach → *Assets* tab (`#outreach/assets`) | everyone reads; owner/admin/operator write; viewer is read-only |
| **Media** group | Outreach → *Setup* | owner/admin edit; others read |
| Attachment strip + "media" chip | Approvals cards, Inbox list + thread, Leads → conversation | read |
| Storage meters | Billing usage card | read |

Code: `src/pages/Assets.tsx`, `src/pages/MediaSettings.tsx`, `src/components/media.tsx` (types, `Thumb`, `AttachmentStrip`, `Lightbox`, `TagEditor`, `uploadAsset`), `src/components/Modal.tsx` (now also the shared dialog focus handling).

### Assets page
- **Storage card**: space used and file count against the plan (`usage` from `GET /api/assets`). At ≥85 % it suggests upgrading; at 100 % uploads are blocked client-side with the reason and an *Upgrade* button (→ Billing).
- **Upload**: drag-and-drop, click, or keyboard (Enter/Space on the zone). Several files are uploaded one after another, each with a progress bar. Optional tags applied to the whole batch.
- **Pre-checks** (before anything is sent): type (JPEG/PNG/WebP, PDF, MP4), per-file cap (image 5 MB, PDF 10 MB, video 16 MB), empty file, file-count limit, storage already full. The server stays the authority: whatever it answers is shown verbatim.
- **Add a link**: https only (the server rejects anything else). Empty name = the server reads the page title.
- **Cards**: fixed 4:3 thumbnail (no layout shift), name, kind/size, tags, "Used in N drafts". Pictures/PDF/video open a lightbox (←/→, Esc); links open in a new tab (`rel="noopener noreferrer"`).
- **Edit** (name, tags), **Delete** (confirmation dialog; the server's 409 text is shown inside the dialog).
- **Empty state** explains what to upload (logo, photos, brochure, website, booking link).

### Setup → Media
- **First message**: Text only / Image with caption / Link only; each non-text mode needs one file (picker offers only valid kinds).
- **Follow-up 1 / 2**: up to 3 files or a link each (applies once follow-ups are switched on).
- **AI replies may attach files** + the tags it may attach for (tag chips show how many files each tag has).
- Plain-language safety note (no media in a number's first 3 days, ~30 % cap while warming up, one message per first contact, caps/hours/STOP always apply).
- Disabled states say why: empty library ("Add files →"), loading, read-only role.
- Files deleted since the last save are dropped from the form with a notice; saving applies it. Validation (`mediaProblem`) blocks saving a mode with no file or AI attach with no tags.

### Attachments in lists/threads
`attachments` + `linkUrl` on drafts, inbox rows and thread messages render as a thumbnail strip (read-only; removing an attachment from a draft is **not** offered because the contract has no endpoint for it). Inbound media arrives as a placeholder (`[image]` + `mediaKind`) and is shown as "They sent a photo. Open WhatsApp on your phone to see it." (files are not downloaded).

## Error mapping
All errors use the server's `detail` text unchanged (`errMessage` in `src/api.ts`).

| Status | Where it appears | Extra UI |
|---|---|---|
| 402 plan/storage limit | upload row | *Upgrade* link to Billing; remaining queued files are skipped with the same message |
| 413 too large | upload row | — |
| 415 unsupported type | upload row | — |
| 429 upload rate limit | upload row | remaining queued files are skipped |
| 507 storage unavailable | upload row | *Upgrade* link; rest of queue skipped |
| 409 delete while attached | inside the delete dialog | — |
| 422 (e.g. link not https) | inside the link dialog | — |
| network error | upload row: "Network error. Check your connection…" | — |

## Accessibility and layout
- Dialogs (`Modal`, lightbox) move focus in, trap Tab, close on Esc (only the top dialog), and return focus to the opener. Radio/checkbox pickers use `role=radio|checkbox` + `aria-checked`; meters use `role=progressbar`; the upload list is `aria-live`.
- 360 px: grid collapses to 2 (then 1) columns; the five Outreach tabs scroll sideways instead of widening the page; two-column form rows collapse to one (an old CSS ordering bug that made the Setup page 401 px wide on phones is fixed).

## Gaps / assumptions
- **Variants**: the backend supports up to N extra versions per asset (`POST /api/assets/{id}/variants`); the UI only shows "N versions" and has no upload/delete for them yet.
- **Billing meters** read the library's own `usage` (`GET /api/assets`) because `/api/billing` carries no media fields.
- Public API: the developers page says `POST /v1/messages` sends text and media is attached by the outreach engine; update it if the API gains a media field.
- Verified against a mock of the contract (headless Chrome, desktop + 360 px), not yet against the live backend.
