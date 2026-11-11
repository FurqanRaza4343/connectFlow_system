# WhatsApp Status (stories): structure aur technical tareeqa

Ye document puraane (legacy) dashboard `http://127.0.0.1:3100` ke **WhatsApp Status** tab ke liye hai. Code `legacy/orchestrator/` mein hai (ye folder git mein nahi, `.gitignore` mein hai). Naye SaaS par port baad mein hoga, uska plan alag hai.

## 1. Ye kya karta hai

Aap ke business ka data (`legacy/data/company_knowledge.md`) aur aap ke "Current offers" parh kar **DeepSeek text likhta hai**, hamara renderer **picture banata hai**, aur **OpenWA** usay aap ke WhatsApp number se **Status (24 ghante ki story)** laga deta hai: abhi, kisi waqt par, ya poore campaign ke taur par (jaise 14 din, har 12 ghante).

Teen tareeqe: **Create** (ek post, apne haath se ya AI ki madad se), **Planner** (poora campaign: AI plan banaye, likhe, aap approve karein), **Autopilot** (roz tay waqt par khud draft ya post).

## 2. Sab se ahm baat: kaun sa kaam kaun karta hai

| Kaam | Kaun | Kaise |
|---|---|---|
| Idea, headline, caption, roadmap likhna | **DeepSeek** (`deepseek-v4-pro`, ya `STATUS_MODEL`) | `src/ai.mjs`: `statusIdeas`, `planCampaign`, `writeCampaignItems` |
| Picture (card) banana | **Hamara code** | `src/render.mjs`: SVG text -> `@resvg/resvg-js` -> PNG 1080x1920 |
| AI background picture (optional) | **Image API** (Together FLUX ya OpenAI) | `src/imageapi.mjs`, sirf background, text nahi |
| WhatsApp par post karna | **OpenWA** | `POST /api/sessions/:id/status/send-image` (ya `send-text`), `DELETE .../status/:id` |
| Waqt par post karna, retry, safety | **Orchestrator** | `src/status.mjs` `statusTick()` har 60 second |

**DeepSeek image nahi bana sakta** (uska output sirf text hai). Isi liye text aur picture alag hain. Text hamesha hum khud likhte hain: AI image models spelling bigaad dete hain ("30% OFF" ko "3O% OFFF").

## 3. Architecture

```
Browser (Status tab: stories.js, planner.js)
   |  /api/stories/*   /api/campaigns/*
   v
dashboard/server.mjs  ----------------------------------------------+
   |                                                                |
   |-- status.mjs   (posts, render, post-now, autopilot, caps)      |
   |      |-- render.mjs     SVG -> PNG (resvg, fonts in container) |
   |      |-- imageapi.mjs   AI background (optional)               |
   |      |-- ai.mjs         DeepSeek (ideas / plan / write)        |
   |      `-- whatsapp/index.mjs -> openwa.mjs -> OpenWA (Status)   |
   |-- campaigns.mjs (slots, plan, write, approve, calendar, stats) |
   `-- db.mjs  SQLite: status_posts, status_campaigns, status_usage |
                                                                    |
scheduler.mjs  every 60 s: statusTick() then campaignTick() <-------+
```

Node `node:sqlite`, koi framework nahi. Wahi ek npm package: `@resvg/resvg-js` (picture banane ke liye; WhatsApp SVG nahi leta aur Node mein image banane ka apna tool nahi).

## 4. Files

- `src/status.mjs`: engine (create/update/post/delete, caps, autopilot, `statusTick`).
- `src/campaigns.mjs`: slots ka hisaab, plan, write, approve/pause/resume/cancel, `calendar()`, `stats()`.
- `src/render.mjs`: 8 designs, rang aur readable text colour, Urdu (RTL), badge, button, footer.
- `src/imageapi.mjs`: provider adapter (`together` / `openai`).
- `src/ai.mjs`: DeepSeek client (`chat`) aur Status ke 3 functions.
- `src/dashboard/server.mjs`: routes. `public/stories.js` (Create, Brand kit, Posts), `public/planner.js` (Planner, calendar, charts).
- `src/selftest-status.mjs`: 88 checks (nakli OpenWA/AI/image servers, asli DB nahi chhoota).

## 5. Data model (SQLite)

**`status_posts`**: ek row = ek Status. Ahm columns: `state`, `headline/sub/badge/cta/caption/link`, `btn_link` (`''`, `'wa'` ya `https://...`), `template`, `accent/dark/ink`, `overlay/pos/plain` (apni picture ke liye), `bg_file` (background ya upload), `image_file` (final PNG), `scheduled_at`, `posted_at`, `expires_at`, `owa_status_id`, `campaign_id/plan_theme/plan_day`, `auto`, `tries`, `error`.

State machine: `draft -> scheduled -> posting -> posted`, ya `failed`, `cancelled`.
- `posting` post bhejne se **pehle** likha jata hai. Agar process beech mein mar jaye ya jawab ka pata na chale, row `failed` ho jati hai aur **khud dobara nahi bheji jati** (do baar story lagne se behtar hai ke aap check karein).

**`status_campaigns`**: brief, days, frequency (`interval_hours` ya `per_day`), window, `start_date`, `state`, `plan_json` (har slot: n, day, time, at, theme, angle), `questions_json`, `summary`, `total/generated`, `busy_since`.
Campaign states: `planning -> asking|planned -> generating -> review -> running -> done`, aur `paused`, `cancelled`, `failed`.

**`status_usage`**: roz ke hisaab se AI ideas, AI pictures, posted, andaazi kharcha.

## 6. Ek post ka safar (Create)

1. Aap fields bharte hain (ya AI ke 3 ideas mein se ek chunte hain). **Preview** `POST /api/stories/preview` se PNG data-URL mangta hai (kuch save nahi hota).
2. **Post now / Schedule / Save draft** -> `createPost()`: row banti hai, background (card, apni upload, ya AI) tay hota hai, PNG `state/status-media/s<id>.png` mein banta hai.
3. Post ke waqt `postNow()`: Dry-run? -> sirf simulate. Session `ready`? -> warna error ya 15 minute baad dobara. Row ko `posting` karo -> OpenWA `send-image` (base64 + caption) -> `posted`, `expires_at` = 24 ghante.
4. **Caption** = aap ka text + contact line + `👉 button text: link`. 1024 se barhe to **link bachta hai, text kat'ta hai**.

## 7. Link aur "button"

Status ki picture par **koi tappable button nahi ho sakta**. Card par button sirf design hai ("Tap the link below ↓"). Asli tappable cheez **caption ka link** hai. "Chat on WhatsApp" = `https://wa.me/<number>?text=...`; number Brand kit se, khali ho to connected session ka number. Sirf `https://` ya `wa` qubool; `javascript:`, `ftp:` wagera refuse.

## 8. Campaign Planner

1. **Slots** (code hisaab lagata hai): `dayTimes()` + `computeSlots()`. "Har 6 ghante" ka matlab window ke andar (jaise 09:00-22:00 -> 09, 15, 21). Roz zyada se zyada 5, poore campaign mein 150 tak, 45 din tak.
2. **Plan** (`planCampaign`, DeepSeek): brief + company data + offers + pichhle 14 din ke headlines. Wapas: ya to **sawal** (zyada se zyada 3, sirf jab bina jawab ke plan na ban sake) ya **roadmap** (har slot ka theme + angle). Mix: tips+service ~40-50%, trust 20%, offer sirf agar maujood, greeting ~10%, aakhri post cta. Code ke checks: theme galat ho to `tip`; offer nahi hai to `offer` -> `service`.
3. **Plan background mein chalta hai** (reasoning model 1-3 minute leta hai): page animated words aur timer dikhata hai, aap page chhor sakte hain. Server restart ho to campaign `failed` ho kar "Plan again" ka button deta hai.
4. **Write** (`writeCampaignItems`): 5-5 posts ki batches. Har post render hokar `draft` banti hai, `scheduled_at` mein uska tay waqt. Dobara chalayein to jo ban chuki unhein dohrata nahi.
5. **Review -> Approve all**: sab drafts `scheduled` ho jate hain (guzre hue waqt wale cancel). Phir `statusTick` unhein waqt par post karta hai. Approve se pehle **kuch post nahi hota**.
6. Pause (scheduled -> draft), Resume, Cancel; sab post ho jayein to campaign `done` aur notification.

## 9. Autopilot

Settings: on/off, times (`11:00,19:00`), roz ki hadd (<=5), *review* (draft aap ka intezar kare) ya *auto* (10 minute tak jitter ke saath khud post). Har slot ka marker **pehle** likha jata hai (crash par do post nahi). Brand kit wahi istemal hota hai jo Create tab mein.

## 10. DeepSeek aur token

- `deepseek-v4-pro` **reasoning** model hai: uski sochne wali tokens bhi `max_tokens` mein ginti hain. Isi liye 14 posts ka plan 6000 se upar chala gaya aur "output cut off" aaya. Ab Status ke calls 12000 se shuru hote hain aur cut-off par **khud double** hokar dobara try karte hain (32000 tak). Balance ka masla nahi tha (balance $1.31 available tha).
- `STATUS_MODEL` (`.env`, optional): Status ke liye tez/sasta model, khali = `MODEL`. Isse planning jaldi hogi; quality ka farq khud parkh lein.
- AI ko sakht rules: sirf knowledge base, offers box aur aap ka brief; koi daam/discount/client **invent nahi**. Offer tabhi jab "Current offers" mein ho ya brief mein likha ho.

## 11. AI picture (optional) aur kharcha

`IMAGE_PROVIDER=together` (FLUX.1 schnell, ~$0.003/picture) ya `openai` (gpt-image-1-mini, `IMAGE_QUALITY=low|medium`, ~$0.006 / $0.015). Daam third-party blogs aur provider pages se hain: key lene se pehle provider ka page dekhein. Hadd: roz aur mahine ki (Brand kit & Autopilot tab). AI picture sirf **background** hai; headline/offer/footer hum upar likhte hain. `IMAGE_API_KEY` sirf `legacy/orchestrator/.env` mein, kabhi chat mein nahi.

## 12. Buttons ka matlab

| Button | Kya karta hai | WhatsApp par asar |
|---|---|---|
| **Post now** | abhi post | live ho jata hai |
| **Cancel** (draft/scheduled par) | post ko plan se nikalta hai (state `cancelled`) | kuch nahi, kyunke post hui hi nahi |
| **Delete from WhatsApp** | live Status ko WhatsApp se hata deta hai | **haan, sab ke liye gayab** |
| **Remove from list** | sirf is dashboard ki list se row aur uski picture files hatata hai | **koi asar nahi**: live Status 24 ghante rehta hai |
| **Edit & repost** | live Status ki **copy** composer mein kholta hai | naya Status lagta hai; purana tab hatta hai jab "Delete the old live Status" tick ho |

## 13. Post karne ke baad caption edit?

**Nahi.** WhatsApp posted Status ko edit karne nahi deta, aur OpenWA mein bhi edit ka endpoint nahi. Isliye hum **Edit & repost** dete hain: copy mein caption/text badal kar naya Status post, aur (tick ho to) purana delete. Nuksan: naye Status ke viewers zero se shuru hote hain.

## 14. Safety

Dry-run on ho to kuch post nahi hota. Session `ready` hona chahiye. Gateway ka 429 (pacing) sirf reschedule karta hai, 6 koshish ke baad `failed`. Mojooda row ko ek hi caller `posting` kar sakta hai. Roz 5 se zyada nahi. Uploads: sirf PNG/JPEG/WebP, 3 MB, magic-bytes check. Link validation. Offers ka sakht rule.

## 15. WhatsApp ki hadein (jo hum badal nahi sakte)

- Status sirf unhein dikhta hai jinhon ne aap ka number save kiya ho (aur jo aap ki privacy list mein hon).
- 24 ghante baad khud khatam. Caption 1024 characters tak.
- **Views nahi milte**: OpenWA ye data nahi deta. Assal nateeja naapne ke liye post mein keyword rakhein ("AI likh kar reply karein") aur aane wali chats ginein.
- OpenWA ka `/chats` page Status ko chhupata hai aur aap ka apna status store nahi karta. Apna status sirf phone mein *Status / Updates -> My status* mein dikhta hai.
- Naya gateway (Baileys) mein audience list (`statusRecipients`, 256 tak) zaroori hai; purana (whatsapp-web.js) aap ki privacy setting istemal karta hai.

## 16. Chalana aur test

- Deploy: `cd infra && docker compose --profile legacy up -d --build orchestrator` (static files image mein bake hoti hain, isliye har tabdeeli par rebuild).
- Test (image ke andar, asli DB/WhatsApp nahi chhoota): `docker run --rm --entrypoint node -e OG_STATE_DIR=/tmp/st <image> src/selftest-status.mjs`.
- Masail: *"DeepSeek output cut off"* (ab khud theek hota hai; phir bhi aaye to `STATUS_MODEL` ya brief chhota karein); *HTTP 400* ka matlab server ne wajah ke saath mana kiya, wajah page par likhi aati hai; website crawl ke DNS masle `docs/LEGACY.md` mein.
