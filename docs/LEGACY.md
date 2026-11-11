# One Group WhatsApp Outreach: poori guide (Roman Urdu)

Ye system aap ki leads ko WhatsApp par personalized message bhejta hai, har 5 ghante mein 5 leads, aur jin ka reply aaye unhein follow-up deta hai. Sab kuch ek dashboard se control hota hai. Ye guide hisson mein hai, aap ko sirf wohi hissa parhna hai jo us waqt chahiye.

1. [Ek nazar mein](#1-ek-nazar-mein)
2. [Roz ka istemal (shuru karne ka tareeqa)](#2-roz-ka-istemal)
3. [Dashboard ke pages, ek ek kar ke](#3-dashboard-ke-pages)
4. [System andar se kaise kaam karta hai](#4-system-andar-se-kaise-kaam-karta-hai)
5. [Settings ki poori list](#5-settings-ki-poori-list)
6. [Docker ke commands](#6-docker-ke-commands)
7. [Server (VPS) par chalana](#7-server-vps-par-chalana)
8. [Backup aur update](#8-backup-aur-update)
9. [Security checklist](#9-security-checklist)
10. [OpenWA ke baare mein zaroori baatein](#10-openwa-ke-baare-mein)
11. [Masle aur unka hal](#11-masle-aur-unka-hal)
12. [Folders aur files ka naqsha](#12-folders-aur-files)
13. [Abhi kya nahi hai](#13-abhi-kya-nahi-hai)

---

## 1. Ek nazar mein

```
   Aap (browser)                         Server / PC par Docker
 ┌───────────────┐        ┌─────────────────────────────────────────────────────────┐
 │  Dashboard    │◄──────►│  orchestrator  (hamara app, port 3100)                    │
 │  :3100        │        │    - leads, settings, drafts (SQLite)                    │
 └───────────────┘        │    - scheduler: har 5 ghante 5 leads                     │
                          │    - DeepSeek se research + message likhwana             │
                          │    - webhook: reply sunna, follow-up banana              │
                          │    - watchdog: OpenWA aur WhatsApp session zinda rakhna  │
                          └──────────────┬──────────────────────────▲────────────────┘
                                         │ send-text (REST)          │ webhook (reply)
                          ┌──────────────▼──────────────────────────┴────────────────┐
                          │  openwa  (WhatsApp gateway, port 2785, sirf localhost)    │
                          │    aap ke linked WhatsApp number se message bhejta hai    │
                          └───────────────────────────┬──────────────────────────────┘
                                                      │
                                                  WhatsApp
```

| Hissa | Kaam | DeepSeek key chahiye? |
|---|---|---|
| **openwa** | WhatsApp se jura rehta hai. Sirf message bhejta aur receive karta hai. | Nahi |
| **orchestrator** | Dimaagh: leads, schedule, AI se message likhwana, approval, follow-up. | **Haan** (`orchestrator/.env`) |
| **caddy** (optional) | Server par HTTPS ke liye. | Nahi |

Dono containers ko ek hi command chalati hai: `docker compose up -d --build`.

---

## 2. Roz ka istemal

### Pehli dafa (PC par)
1. Docker Desktop chal raha ho.
2. Project folder mein terminal kholein aur chalayein: `docker compose up -d --build`.
3. Browser mein `http://localhost:3100/dashboard` kholein. Password `orchestrator/.env` mein `DASHBOARD_PASSWORD=` ke baad likha hai.
4. **Overview** page par dekhein ke WhatsApp *Connected* hai. Agar QR nazar aaye to phone mein WhatsApp, Settings, Linked devices, Link a device se scan karein.
5. **Leads** page par apni leads import karein (CSV upload ya Google Sheet link).
6. **AI & Templates** page par **Preview** se kuch leads ke messages dekhein. Pasand na aayein to "Message rules" mein likh kar theek karein (neeche detail hai).
7. **Settings** mein: batch size (5), gap (5 ghante), din ke ghante (9 se 8), roz ki limit dekhein.
8. Messages theek lagein to **Settings** mein *Dry-run* band karein, aur **Overview** par **Resume scheduler** dabayein.

### Pehle hafte ka mamool (approval mode)
Shuru mein "First messages need my approval" on rehta hai:
1. Har 5 ghante ke baad **Approvals** page par 5 messages aate hain (menu mein laal number dikhta hai).
2. Aap har message parh kar edit kar sakte hain, phir **Approve & send**. Ya **Approve all**.
3. Approve hone par message foran nahi jata. Wo ek ek kar ke random gap (60 se 180 second) ke saath jate hain, taake insani lagey.
4. Jab messages se mutmaeen ho jayein, Settings mein approval band kar den. Phir batch ke waqt khud jayenge.

### Reply aane par
- Lead ka reply **Activity** mein dikhta hai aur uska status `replied` hota hai.
- AI jawab ka draft likhta hai. Agar Settings mein *Auto follow-up* on hai aur baat aam hai (jaise tafseel poochna), to 15 se 45 second baad khud bhej deta hai.
- Agar baat pricing, complaint, contract jaisi ho, ya auto-follow-up band ho, to draft **Approvals** mein aata hai (`NEEDS HUMAN` ka nishan ke saath). Aap edit karke bhej dein.
- Agar lead "stop", "not interested", "mat bhejein" jaisa kuch likhe to status `stop` ho jata hai aur us ko dobara kabhi message nahi jata.

---

## 3. Dashboard ke pages

### Overview
- **WhatsApp ka status:** `Connected` ya QR / disconnected. QR hone par yahin QR image dikhti hai aur khud refresh hoti hai. "Start / reconnect" button session dobara chalata hai.
- **Aaj ke first messages** (jaise `3 / 15`): roz ki limit mein se kitne ho chuke.
- **Counts:** total, new (intezar mein), queued (approval ke liye), contacted, replied, opt-out.
- **Scheduler:** Paused ya Running, aakhri batch kab gaya, agla kab hoga. **Pause/Resume** aur **Run a batch now** (abhi ek batch, interval ka intezar kiye bina).
- **Peeli warning patti:** Dry-run on ho to "kuch bheja nahi jayega" ka elaan, WhatsApp disconnect ho to uski wajah.
- Ye page har 10 second mein khud update hota hai.

### Leads
- **Search** (company, type, number) aur **status filter**.
- **+ Add lead:** ek lead khud daalein. Phone kisi bhi format mein (`0300 1234567`, `+92 300 1234567`), system khud `923001234567` banata hai.
- **Import CSV:** file chunein. Aap ki apni sheet ke headers bhi chalte hain (Company, Role, Google Maps Address, Phone (Google Maps / web), Website, Email, LinkedIn, Facebook, CEO / Leadership, Google Rating, Source chain, Notes) aur hamare headers bhi (Business Type, WhatsApp Number, Status...). Jo column samajh na aaye wo ignore hota hai.
- **Import Google Sheet:** sheet ka link paste karein. Sheet ko pehle *Share, General access, Anyone with the link, Viewer* karna hota hai. Phir link do, system CSV bana kar import karta hai. Alag tab chahiye to link mein `gid=` wala tab kholein.
- Import mein duplicate (wohi WhatsApp number) skip ho jate hain. "Not listed / Not found / n/a" jaisi values khali maani jati hain.
- Kisi lead par click karein: poori conversation, AI ki research, aur details edit (status bhi badal sakte hain, jaise `hold` kar dein to us ko kabhi message nahi jayega).

### Approvals
- Har draft: kis ko, kya likha, status, aur (reply ho to) lead ne kya kaha.
- **Save edit**, **Approve & send**, **Discard**. Discard hui *pehli* message wali lead wapas `new` ho jati hai (dobara batch mein aa sakti hai).
- **Approve all first messages:** sab pending ek saath approve, magar bhejna ek ek kar ke gap ke saath hota hai.
- Roz ki limit poori ho jaye to bacha hua draft `pending` mein rehta hai ("Daily limit reached") aur agle din dobara approve karna hota hai.
- Dry-run on ho to approve refuse hota hai (bhulne se bachne ke liye).

### Activity
Aakhri 300 messages: waqt (UTC), kya (First message, Follow-up, Lead replied), kis lead ke saath, aur text. Company ke naam par click karke us lead ki conversation khulti hai.

### AI & Templates
- **Message rules:** apni zaban mein likhein, jaise:
  - "Message 60 lafzon se chhota rakho."
  - "Hamesha 15 minute ki call offer karo, Readiness Score ka zikr mat karo."
  - "Agar lead Roman Urdu likhe to Roman Urdu mein jawab do."
  Ye rules AI ko diye jate hain. Facts (company ki maloomat) ke qaidon se upar nahi, yaani AI phir bhi price ya client ka naam invent nahi karega.
- **Preview:** ek lead chunein, **Preview message** dabayein. Research aur message ~30 second mein aata hai. Kuch bheja nahi jata, lead ka status nahi badalta.
  Pehle rules **Save** karein, phir Preview, taake naye rules lagein.
- **Fallback template:** agar AI (DeepSeek) us waqt kaam na kare to ye text bheja jata hai. `{{company}}` aur `{{sender}}` khud bhar jate hain.
- **Company knowledge:** `data/` folder ki `.md` files. AI sirf in ke facts par bharosa karta hai. Services, links, offer badlein to agla message naye facts se banega. Nayi `.md` file `data/` folder mein rakh dein to wo bhi shamil ho jati hai.

### Settings
Poori list [Hissa 5](#5-settings-ki-poori-list) mein hai. Save par foran lag jati hain, restart nahi chahiye.

---

## 4. System andar se kaise kaam karta hai

### 4.1 Ek lead ki zindagi (status)

| Status | Matlab | Kab hota hai |
|---|---|---|
| `new` | Message ke liye tayyar | Import ke baad. Is ke liye valid WhatsApp number zaroori hai |
| `queued` | Message likha gaya, aap ki manzoori ka intezar | Approval mode mein batch ke baad |
| `contacted` | Pehla message chala gaya | Bhejne ke baad |
| `replied` | Lead ne jawab diya | Reply aane par |
| `stop` | Lead ne mana kar diya | "stop / not interested / unsubscribe / mat bhejein..." likhne par |
| `no_whatsapp` | Number WhatsApp par nahi hai | Bhejne se pehle check mein |
| `failed` | Bhejne mein masla aaya | Error par (wajah Notes mein likhi hoti hai) |
| `hold` / `verify` | Aap ne rok rakha hai | Haath se (aise leads kabhi nahi bheje jate) |

Sirf `new` + valid WhatsApp number wali leads batch mein shamil hoti hain.

### 4.2 Phone number ke qaide
- Ek cell mein kai numbers hon (`;` ya `or` se) to pehla *mobile* number liya jata hai.
- Pakistan ka sirf mobile (`923xx...`) chalta hai. Landline (jaise `+92 21 ...`) aur 0300 jaise formats ko `+92` banaya jata hai.
- Settings ki **Allowed country codes** (default `92`) se bahar ke number (jaise US) skip hote hain. Doosre mulk ke leads chahiye to wahan code add karein (`92,971`).
- Bhejne se pehle OpenWA se poocha jata hai ke ye number WhatsApp par hai ya nahi.

### 4.3 Batch ka hisaab
Har minute scheduler dekhta hai:
1. Kya Paused to nahi? Kya abhi *active hours* ke andar hain? (waqt `Asia/Karachi` ke hisaab se; raat ko paar karne wali window bhi chalti hai)
2. Aakhri batch ko `Hours between batches` (5) guzar gaye? Pehli dafa ho to foran.
3. Batch mein `min(batch size, aaj ki bachi hui limit)` leads li jati hain. Bachi hui limit = `Max first messages per day`, minus aaj ke bheje hue, minus approval ke intezar mein pare drafts.
4. Har lead ke liye: research, message likhna, phir approval queue mein ya seedha bhejna.

Aakhri batch ka waqt database mein save hota hai, to app restart hone se batch dohra nahi chalta.

### 4.4 Research aur message kaise banta hai
1. **Research:** sheet ka data (naam, type, role, address, website, leadership, notes) + agar website ho to us ka text (pehla ~3000 characters). DeepSeek is se chhota factual profile banata hai. Jo baat sabit na ho wo "(unverified)" likhi hoti hai. (Web search nahi hota; DeepSeek ke saath ye option nahi.)
2. **Message:** DeepSeek ko ye milta hai: research, company ki knowledge (`data/*.md`), hamare fixed qaide, aur aap ke *Message rules*. Fixed qaide: sirf facts, "Hi <Company> team," se shuru, 3 se 5 chhoti lines, ek hi CTA (free AI Readiness Score ya chhoti call), opt-out line ("reply STOP"), aakhri line par "Abdullah from One Group", hiring ka zikr neutral (AI ko unki team ke madadgar ke taur par dikhana, replacement ke taur par nahi).
3. **Zaban:** English professional. Agar lead Roman Urdu mein likhe to reply Roman Urdu mein.
4. Agar DeepSeek us waqt fail ho (jaise net ka masla) aur fallback template ho, to wo bheja jata hai.

### 4.5 Reply ka raasta
1. Lead ka message WhatsApp se OpenWA ke paas aata hai.
2. OpenWA hamare `/webhook` ko bhejta hai, signature (`X-OpenWA-Signature`, HMAC) ke saath. Galat signature par `401` aur message ignore.
3. Hum dekhte hain: group ya apna message to nahi? lead pehchani gayi? Agar lead `contacted / replied / queued / stop` mein nahi to ignore (anjaan logon ke messages par kuch nahi hota).
4. Pehle keywords check: stop, unsubscribe, remove me, do not contact, not interested, no thanks, mat bhej, band kar, nahi chahiye, na bhejein, ruk jao. Mil gaya to status `stop`, us ke baqi drafts discard.
5. Warna DeepSeek poori conversation dekh kar jawab likhta hai aur batata hai ke ye *needs_human* hai ya nahi.
6. Auto-follow-up on aur `needs_human` na ho to 15 se 45 second baad bhej diya jata hai, warna draft Approvals mein.

### 4.6 Ban se bachao ke andar ki cheezein
- Approved messages ek ek karke, random gap ke saath.
- Roz ki limit (hamari taraf se) **aur** OpenWA ki apni pacing (neeche Hissa 10).
- Har message mein opt-out line aur STOP ka ehtram.
- Reply se pehle thori der ("typing" jaisa).
- Dry-run aur approval mode default on.

### 4.7 Watchdog (khud ko theek rakhna)
Orchestrator start hote hi (aur har minute) ye karta hai:
- OpenWA ka intezar, jab tak wo taiyar na ho.
- WhatsApp session na ho to banana. Hamara webhook register na ho to register karna. Purana (galat address wala) webhook ho to hata dena.
- Session `disconnected / created / failed` ho to khud dobara start karna (2 minute mein ek dafa se zyada nahi).
- Agar QR chahiye (login khatam ho gayi) to Overview par QR dikhta hai.

Is ki wajah se PC/server restart ke baad aap ko kuch nahi karna: dono containers khud uthte hain aur WhatsApp khud wapas connect hota hai.

---

## 5. Settings ki poori list

Ye sab dashboard ki **Settings** (aur **AI & Templates**) se badalti hain. `orchestrator/.env` ki values sirf *pehli dafa ka default* hain; dashboard mein save karne ke baad database ki value jeetti hai.

| Setting (dashboard) | Default | Matlab |
|---|---|---|
| Leads per batch | 5 | Ek batch mein kitni leads |
| Hours between batches | 5 | Do batch ke darmiyan kam az kam kitne ghante |
| Active from / until | 09:00 / 20:00 | Sirf in ghanton mein batch chalta hai |
| Timezone | Asia/Karachi | Active hours isi waqt ke hisaab se |
| Max first messages per day | 15 | Roz ki hadd (pehle message ki) |
| Min / Max gap (sec) | 60 / 180 | Ek ke baad doosra message kitni der baad |
| Sender name | Abdullah | "Abdullah from One Group" |
| Allowed country codes | 92 | Kin mulkon ke numbers ko message jaye |
| First messages need my approval | on | Pehle approval, phir bhejna |
| Auto follow-up | off | Reply par khud jawab (needs-human nahi) |
| Dry-run | on | Likhna haan, bhejna nahi |
| Message rules | khali | AI ke liye extra hidayat |
| Fallback template | maujood | AI fail hone par ka text |
| Paused (Overview ka button) | **paused** | Scheduler band/chalu |

`orchestrator/.env` mein ye hain (secrets bhi yahin):

| Variable | Matlab |
|---|---|
| `DEEPSEEKER_API_KEY` (ya `DEEPSEEK_API_KEY`) | DeepSeek ki key |
| `MODEL` | `deepseek-v4-pro` (sasta aur tez: `deepseek-flash`) |
| `AI_BASE_URL` | default `https://api.deepseek.com` |
| `OPENWA_API_KEY` | OpenWA ki admin key (`openwa/data/.api-key` ki line) |
| `OPENWA_SESSION` | session ka naam (`outreach`) |
| `WEBHOOK_SECRET` | koi bhi 16+ characters; webhook ke signature ke liye |
| `DASHBOARD_PASSWORD` | dashboard ka password |
| `WEBHOOK_PORT` | 3100 |
| `LEADS_CSV` | pehli dafa ke import ki file (`data/leads.csv`) |

Root `.env` (docker compose ke liye, optional): `TZ`, `DASHBOARD_BIND`, `DASHBOARD_PORT`, `OPENWA_PORT`, `OPENWA_IMAGE`, `OPENWA_MEM_LIMIT`, `SEND_PACING_*`, `DOMAIN`. Misal ke liye `.env.example` dekhein.

---

## 6. Docker ke commands

Hamesha project folder (jahan `docker-compose.yml` hai) mein chalayein.

| Kaam | Command |
|---|---|
| Sab kuch chalana (aur build) | `docker compose up -d --build` |
| Haalat dekhna | `docker compose ps` |
| App ke live logs | `docker compose logs -f orchestrator` |
| OpenWA ke logs | `docker compose logs -f openwa` |
| Dono ko restart | `docker compose restart` |
| Sirf app restart (code/`.env` badalne ke baad) | `docker compose up -d --build orchestrator` |
| Band karna (data bachta hai) | `docker compose stop` |
| Dobara chalana | `docker compose start` |
| Poora hata dena (container), data folders ko haath nahi lagta | `docker compose down` |
| HTTPS ke saath (server) | `docker compose --profile https up -d` |

Container ke andar kuch chalana: `docker compose exec orchestrator sh`.

**Windows:** agar `docker` command na mile to `C:\Program Files\Docker\Docker\resources\bin\docker.exe` use karein, ya naya terminal kholein.

**PC restart ke baad:** Docker Desktop ko chalna chahiye. Docker Desktop ki settings mein *Start Docker Desktop when you sign in* on kar den. Containers par `restart: unless-stopped` hai, to Docker uth-te hi wo bhi uth jate hain.

---

## 7. Server (VPS) par chalana

PC band hone par sab ruk jata hai, is liye asli istemal ke liye server behtar hai (Ubuntu 22.04/24.04, kam az kam 2 GB RAM, 2 vCPU).

### 7.1 Server tayyar karna
```bash
# Docker lagana
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER        # phir logout/login

# Firewall: sirf SSH aur web
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### 7.2 Project server par pahunchana
Poora `lead_gen_whatsapp` folder server par copy karein (git, `scp`, ya `rsync`), lekin `node_modules` aur `orchestrator/logs`, `orchestrator/qr` chhor dein.

```bash
rsync -av --exclude node_modules --exclude orchestrator/logs --exclude orchestrator/qr \
  ./lead_gen_whatsapp/ user@SERVER:/opt/lead_gen_whatsapp/
```

### 7.3 WhatsApp login: copy karein ya dobara scan karein
- **Aasan aur mehfooz:** `openwa/data/sessions` copy **na** karein. Server par naya QR scan karein. Naya folder khali ho to Overview par QR aa jayega. (Ek hi WhatsApp login do jagah ek waqt mein chalana ban ka sabab ban sakta hai. PC wala stack band hona chahiye.)
- Agar copy karna hi ho to PC par `docker compose stop` karke poora `openwa/data` copy karein, aur PC par dobara kabhi start na karein.

### 7.4 Chalana
```bash
cd /opt/lead_gen_whatsapp
docker compose up -d --build
docker compose logs -f orchestrator      # "OpenWA is configured" dikhna chahiye
```
Phir dashboard kholein (neeche HTTPS), Overview par QR scan karein.

### 7.5 Dashboard ko HTTPS par kholna (domain ke saath)
1. Domain ka DNS A record server ke IP par.
2. Root `.env` mein: `DOMAIN=outreach.aapkadomain.com`
3. `docker compose --profile https up -d`
4. `https://outreach.aapkadomain.com/` kholein (dashboard ab seedha root par hai; `/dashboard` purana raasta bhi chalta hai). Certificate Caddy khud leta aur renew karta hai.
5. Caddy `/webhook` ko bahar se `404` karta hai; wo sirf Docker ke andar chalta hai.
6. **OpenWA ka apna admin page** `https://wa.outreach.aapkadomain.com/` par (DNS ka doosra A record `wa.` bhi server ke IP par). Ye basic-auth password ke peeche hai: root `.env` mein `WA_ADMIN_USER` aur `WA_ADMIN_HASH` (hash banane ka command `.env.example` mein likha hai). Roz ke kaam (QR, status) ke liye aap ko isay kholne ki zaroorat nahi, hamare dashboard ki Overview mein sab hai.

Domain na ho to dashboard ko SSH tunnel se kholein: `ssh -L 3100:127.0.0.1:3100 user@SERVER`, phir PC ke browser mein `http://localhost:3100/dashboard`. Isi tarah OpenWA ka apna dashboard: `ssh -L 2785:127.0.0.1:2785 user@SERVER`.

### 7.6 Linux par permissions
Orchestrator ke container ka entrypoint khud `state/` aur `data/` ki ownership theek kar deta hai. Kuch karna nahi.

---

## 8. Backup aur update

### Kya bachana zaroori hai
| Folder / file | Kya hai | Kho jaye to |
|---|---|---|
| `orchestrator/state/` | leads, conversations, drafts, settings | Saari leads aur history gayab |
| `openwa/data/` | WhatsApp login, OpenWA DB, admin key | WhatsApp dobara QR se link karna parega |
| `data/` | company knowledge | Wohi files dobara likhni hongi |
| `orchestrator/.env` | **secrets** | Keys dobara banani hongi |

### Backup lena
- Windows: `powershell -File scripts\backup.ps1`
- Linux: `sh scripts/backup.sh`

Script kuch second ke liye containers rok kar (taake SQLite sahi copy ho) `backups/` mein archive banati hai, phir containers dobara chalu karti hai. Archive mein secrets hain, usay mehfooz rakhein aur kisi ko na bhejein. Server par isay `cron` se hafte mein ek dafa chalana aur archive kisi doosri jagah copy karna achha rehta hai.

### Wapas laana (restore)
Containers `docker compose down` karein, backup se folders wapas rakhein, phir `docker compose up -d`.

### Update
- Hamara code badla: `docker compose up -d --build orchestrator`
- OpenWA naya version: root `.env` mein `OPENWA_IMAGE=ghcr.io/rmyndharis/openwa:<version>` likhein (ya `latest`), phir `docker compose pull openwa && docker compose up -d`. Naya version laganay se pehle backup len.

---

## 9. Security checklist

- [ ] `orchestrator/.env` kisi ko na bhejein. Is mein DeepSeek key, OpenWA admin key aur dashboard password hain. Screenshot mein bhi na aaye (ek dafa DeepSeek key screenshot mein dikhi thi; agar wo screenshot share hua ho to DeepSeek se key badal lein).
- [ ] `DASHBOARD_PASSWORD` lamba aur alag rakhein. 5 galat koshishon par 10 minute ka lock lagta hai.
- [ ] Dashboard ko internet par seedha (HTTP) na kholein. HTTPS profile ya SSH tunnel use karein.
- [ ] OpenWA (`:2785`) sirf `127.0.0.1` par hai; isay public na karein.
- [ ] Server ka firewall: sirf SSH, 80, 443.
- [ ] Orchestrator ko OpenWA ki *admin* key ke bajaye ek *operator* key dena behtar hai (OpenWA dashboard, API Keys, role Operator, sirf session `outreach`). `.env` mein `OPENWA_API_KEY` badlein aur `docker compose up -d orchestrator`.
- [ ] Regular backup, aur backup archive ko mehfooz rakhein.
- [ ] Lead ke data par apne mulk ke qawaneen (consent, spam, privacy) ka dhyan rakhein.

---

## 10. OpenWA ke baare mein

**Roles** (OpenWA ke API Keys page par):
| Role | Kya kar sakta hai |
|---|---|
| Admin | Sab kuch (keys banana, session banana, proxy, infrastructure) |
| Operator | Message bhejna, sessions/webhooks chalana. Keys manage nahi |
| Viewer | Sirf dekhna |

Har banda ek *API key* hai (alag user accounts nahi). Key kisi ek session ya chats tak mehdood ho sakti hai. Dashboard mein wohi key login box mein dalte hain.

**Sessions:** ek session = ek WhatsApp number. Kai numbers chala sakte hain (har ek ko ~300 se 500 MB RAM). Hamara app abhi sirf ek session (`outreach`) use karta hai.

**Limits:**
- OpenWA ka API rate limit (10/second, 100/minute, 1000/ghanta) sirf misuse se bachao hai.
- **Pacing (on hai):** din ka total (20, 40, 80, 160, 320 session ki umar ke hisaab se) aur cold messages ki alag had (5, 10, 15, 20, 30, 40). Hadd paar hone par OpenWA `429` deta hai aur hamara batch us din rok jata hai. *Dhyan:* umar OpenWA ke session banne ki tareekh se gini jati hai, to nayi linked SIM par bhi ye "purana session" ho sakta hai. Is liye hamari apni `Max first messages per day` se bhi hifazat rakhein.
- Asli hadd WhatsApp ki hai. Ye aam taur par kuch messages/minute theek maanti hai, hazaron/ghanta nahi. Anjaan logon ko pehla message sab se risky hai.

**Proxy:** session ka traffic kisi aur IP se guzarna. Server (VPS) par datacenter IP zyada flag hote hain, wahan residential proxy madad kar sakta hai. Ghar ke PC par zaroorat nahi. Lagane ke liye admin key.

**Infrastructure page:** database (SQLite/PostgreSQL), engine (`whatsapp-web.js` ya `baileys`), Redis, storage, aur export/import backup. Abhi kuch badalne ki zaroorat nahi.

---

## 11. Masle aur unka hal

| Masla | Wajah | Hal |
|---|---|---|
| OpenWA dashboard "Invalid API key" | Aap ne DeepSeek key (`sk-...`) paste ki | `openwa/data/.api-key` wali line (`owa_k1_...`, 71 characters) paste karein |
| Overview par WhatsApp `unreachable` | Docker band hai ya OpenWA abhi uth raha hai | Docker Desktop chalayen, `docker compose ps`; 1 minute intezar |
| Status `disconnected` | Restart ya net ka masla | Khud theek hota hai (watchdog). Na ho to Overview par "Start / reconnect" |
| Status `qr_ready` | Login khatam (phone se device hata diya) | Overview par QR scan karein |
| Phone mein "linking passkey" mangta hai | WhatsApp ki nayi shart, OpenWA ke bas mein nahi | Doosra number istemal karein ya WhatsApp ki shart dekhein |
| Batch chalta hi nahi | Scheduler *Paused*, ya active hours se bahar, ya roz ki limit poori | Overview dekhein: Paused/Resume, "outside active hours" |
| Approve dabane par "Dry-run is ON" | Safety | Settings mein Dry-run band karein |
| Draft par "Daily limit reached" | Aaj ki hadd poori | Kal dobara Approve karein, ya Settings mein limit badhayein (ehtiyat se) |
| `429` / `SEND_PACING_LIMITED` | OpenWA ki pacing | Intezar karein; limit ko bypass mat karein |
| Lead `no_whatsapp` ho gayi | Number WhatsApp par nahi | Number theek karke status `new` karein |
| Lead `failed`, Notes mein error | Bhejne mein error | Notes parhein, status `new` kar ke dobara |
| Reply aaya magar kuch nahi hua | Sender anjaan, group, ya lead active status mein nahi | `docker compose logs orchestrator` mein `[in]` lines dekhein |
| Webhook delivery fail | Galat URL / secret | `docker compose logs orchestrator`; app restart karein, watchdog dobara register karta hai |
| DeepSeek error / "output cut off" | Key ya balance / net | Logs dekhein; `MODEL=deepseek-flash` try karein; key/balance check |
| Dashboard login ke baad foran bahar | Cookie 7 din ki hai; ya password badla | Dobara login |
| `docker` command nahi mila (Windows) | PATH purana | Naya terminal, ya poora path istemal karein |
| Pehla message pahuncha nahi | WhatsApp ki apni policy (OpenWA ke docs mein bhi hai) | Dubara dekhein; cold messages ki had kam rakhein |

Logs hamesha sab se pehli madad hain: `docker compose logs -f orchestrator` aur `docker compose logs -f openwa`.

---

## 12. Folders aur files

```
lead_gen_whatsapp/
├── docker-compose.yml       # poora stack (openwa + orchestrator + optional caddy)
├── Caddyfile                # HTTPS (server)
├── .env.example             # compose ke options (optional root .env)
├── README.md                # ye guide
├── data/
│   ├── company_knowledge.md # AI ke liye company ki maloomat (dashboard se edit)
│   └── leads.csv            # pehli dafa ki import (24 leads)
├── scripts/
│   ├── backup.ps1 / backup.sh
├── openwa/                  # OpenWA ka clone + apna data
│   └── data/                # WhatsApp login, DB, .api-key  (BACKUP)
└── orchestrator/
    ├── Dockerfile, docker-entrypoint.sh, .dockerignore
    ├── .env                 # secrets (BACKUP, share mat karein)
    ├── .env.example
    ├── state/               # SQLite: leads, messages, drafts, settings (BACKUP)
    └── src/
        ├── index.mjs        # chalane ke commands (daemon, run, import, setup)
        ├── config.mjs       # .env se defaults
        ├── db.mjs           # database ke tables
        ├── store.mjs        # leads + settings, CSV import, phone ke qaide
        ├── ai.mjs           # DeepSeek: research, pehla message, reply
        ├── outreach.mjs     # batch, approval queue, bhejna
        ├── scheduler.mjs    # har 5 ghante ka batch, active hours
        ├── watchdog.mjs     # OpenWA/WhatsApp ko theek rakhna
        ├── webhook.mjs      # reply sunna, STOP, follow-up
        ├── openwa.mjs       # OpenWA ka API client
        ├── leads.mjs        # CSV parser, phone normalize (+ purana Google Sheets code)
        ├── dashboard/       # server.mjs (API, login) + public/ (page)
        └── selftest-*.mjs   # tests (neeche)
```

### Tests chalana
Dashboard chal raha ho tab, `orchestrator/` folder mein:
```
node --env-file=.env src/selftest-dashboard.mjs     # login, leads, import, settings, security checks (kuch WhatsApp ko nahi bhejta)
node --env-file=.env src/selftest-webhook.mjs       # webhook signature (alag chalta server, 3100 khali ho)
```
Sirf tests ke liye `docker compose stop orchestrator` karke `selftest-webhook` chalana parta hai (port 3100 khali ho), ya sirf dashboard test chalayein.

Terminal se ek dry-run batch (docker ke bahar, Node 22.5+ chahiye): `cd orchestrator && npm run dry-run`.

---

## 13. Abhi kya nahi hai

- **Google Sheet se live sync:** abhi sheet sirf *import* hoti hai (link ya CSV). Nayi leads ke liye wahi sheet dobara import karein; duplicate khud skip hote hain. Roz khud-ba-khud sheet padhna (service account) baad ka kaam hai.
- **Un ke liye follow-up jin ka reply nahi aaya:** abhi sirf reply wale leads ko follow-up hota hai (aap ka maqsad yehi tha). "3 din baad yaad dihani" jaisa schedule alag feature hai.
- **Web search se research:** DeepSeek ke saath nahi; research sheet data aur website ke text par hai.
- **Ek se zyada WhatsApp numbers mein rotation:** OpenWA support karta hai, magar hamara app abhi ek session use karta hai.
- **Dashboard mein AI se chat karke rules badalna:** abhi rules khud likhne hote hain, aur Preview se parkhna hota hai (draft par Regenerate se instruction de sakte hain).
- **LinkedIn / Google Maps scraping:** nahi (unki terms aur blocking). Sirf website + search + AI.
- **Asli leads par live batch ka test:** sab kuch dry-run aur apne number par parkha gaya hai; pehle live batch par approval mode ke saath khud nazar rakhein.

---

## 14. Naye features (v2)

Dashboard ab `http://localhost:3100/` par hai (pehle `/dashboard`; wo bhi chalta hai).

### 14.1 Naya dashboard
- **Overview:** KPI cards (total, bhejne ke liye tayyar, contacted, replied + reply rate, opt-out, bina number), 14 din ka chart (sent vs replies), pipeline ka donut, scheduler ka live **countdown**, roz ki limit ka ring, aur **Last batch** ka report ("5 drafts bane, 4 leads number ke saath baqi, 14 bina number").
- **Notification bell:** batch complete, lead ka reply, NEEDS HUMAN, send fail, WhatsApp session disconnect, data improvement khatam. Settings mein *Admin WhatsApp number* dein to batch report aur reply alert WhatsApp par bhi aata hai.
- **Approvals:** batch ke hisaab se group; har draft par badge (`AI`, `fallback template`, `edited`); **Regenerate** (instruction likh kar, ya "Shorter / Roman Urdu / Offer a 15-min call" jaise quick buttons); fallback drafts "Approve all" mein shamil nahi hote.
- **Leads:** pagination (50 per page, Settings mein badal sakte hain), sorting, filters (status, number hai/nahi, improved/nahi, list), bulk actions (hold, new, delete, improve), export CSV.

### 14.2 Lists: naya aur purana data mix nahi hota
Har import (CSV ya Google Sheet) ek **list** banata hai (aap naam dete hain). Leads table mein list ka chip dikhta hai aur list par click karke sirf wahi leads dekhte hain. "manage" se list rename ya hata sakte hain. Duplicate (wohi WhatsApp number, ya number na ho to wohi company + address) khud skip hota hai.

### 14.3 Import kaise kaam karta hai (AI kahan lagti hai)
1. Pehle header **alias table** se pehchane jate hain (Company, Phone, Website, "Google Maps Address"... sab ke kai naam).
2. Jo columns table samajh na paye, unke liye DeepSeek ek dafa mapping suggest karta hai. **Aap ko wo mapping dikhai jati hai** aur aap badal sakte hain, phir hi import hota hai.
3. Rows ke andar ki cheezein (phone ka format, "Not listed" jaise lafz, ek cell mein kai numbers) **code** theek karta hai, AI nahi: wo tez, sasta aur har dafa ek jaisa hota hai.

### 14.4 Improve data (scraping) kaise hoti hai
Do button: **Improve all data** (Leads page ke header par) aur **Improve this lead with AI** (kisi lead ke andar).
1. Website nahi hai to **SearXNG** (free, open-source meta-search, aap ke server par chalta hai) se company ka official site, LinkedIn, Facebook dhoondta hai. Directory sites (Indeed, ZoomInfo...) ignore hoti hain.
2. Website ke home + contact/about/team pages (zyada se zyada 5, ek hi domain) parh kar emails, phones, social links nikalta hai. Cloudflare ke "email protection" ke peeche chhupe emails bhi decode hote hain.
3. DeepSeek is text se saaf JSON banata hai (email, phone, address, leadership, services, size).
4. Merge: **sirf khali fields bharti hain**, maujooda value overwrite nahi hoti (Settings mein option hai). Naya mobile number mile aur lead ka koi number na ho to WhatsApp number ban jata hai.
5. Kya mila, kahan se mila, aur kaun se steps chale: lead ke andar *Data & research* tab mein dikhta hai. Ek lead mein ~20 se 40 second lagte hain; "all" background mein chalta hai, progress bar aur Cancel button ke saath.

**Hadd (sach):** LinkedIn, Facebook aur Google Maps ke andar ka data nahi nikalta (wo automated access block karte hain, aur unki terms bhi mana karti hain). Email/phone sirf tab milte hain jab company khud website par likhti ho. Site bot ko block kare to wo skip hoti hai; hum browser ban kar bypass nahi karte.

### 14.5 Pricing aur needs-human
Settings mein *Always hold pricing / quote questions for me* (default on): price, cost, rates, quote, budget jaise sawal par AI jawab ka draft likhta hai magar bhejta nahi; aap ko notification milti hai.

### 14.6 AI ka network error
DeepSeek tak net na pahunche (`fetch failed`) to ab 3 dafa retry hota hai. Phir bhi fail ho to batch fallback template se draft banata hai, lekin us par **fallback** ka nishan lagta hai, Approvals mein warning aati hai, aur wo kabhi khud nahi bheja jata. Ek click se Regenerate karein.

### 14.7 Naye containers
`searxng` (internal search, bahar se khula nahi) docker-compose mein add hua hai. Pehli dafa image download hoti hai (~200 MB). Isay band karna ho to enrichment sirf unhi leads par chalegi jin ki website pehle se maujood hai.

### 14.8 Ek technical baat
Database SQLite hai, aur journal mode `DELETE` rakha gaya hai (WAL nahi): Docker Desktop (Windows) ke bind-mount par WAL doosre process ko file kholne nahi deta. Is ki wajah se `docker compose exec` se bhi database khul sakta hai.

### 14.9 Improve data: ghalat company se bachao (verification)
Bohot se naam aam hote hain ("HR Blend" Karachi mein bhi hai aur kisi aur mulk mein bhi). Is liye jo website system **khud dhoondta** hai, wo tab hi use hoti hai jab sabit ho jaye ke wo isi company ki hai: ya to site isi lead ke Facebook/LinkedIn page ka link de, ya site mein lead ka sheher / Pakistan likha ho (ya `.pk` ho). Expired / parked / for-sale domain (jaise DropCatch) mustarad hote hain. Jo site aap ne khud lead mein likhi ho, us par bharosa kiya jata hai. Reject hone par lead ke *Data & research* tab mein wajah likhi hoti hai.

Jis lead ki website hi nahi hoti (chhoti companies), uske liye system search ke **snippets** (Facebook, Instagram, job sites) se email/phone nikalta hai, aur wo "lower confidence" likha jata hai. Misal: HR Blend ka email aur number unki Instagram bio aur Facebook job posts se mila. Aise numbers ko pehle message se pehle ek nazar dekh lein.

Emails saaf kiye jate hain: `%20` jaise nishan hatte hain, aur `@example.com`, `name@domain.com`, `noreply`, `sentry` jaise placeholder nikal diye jate hain. Company ke apne domain ki email pehle aati hai.

### 14.10 SearXNG ke engines
`searxng/settings.yml` mein sirf wo engines on hain jo is PC se test mein chale: **bing, startpage, mojeek, yahoo**. Google (access denied), DuckDuckGo (CAPTCHA), Brave (too many requests) aur Qwant (denied) band kar diye hain, kyunke wo natayej ke bajaye kachra ya khali jawab dete the. Kabhi search kharab lage to check karein: container ke andar se `.../search?q=test&format=json&engines=bing` aur JSON ka `unresponsive_engines` dekhein.

### 14.11 DNS (container ka internet)
Docker Desktop ka apna DNS forwarder is PC par fail ho raha tha (10 mein se 0 lookups), jis se DeepSeek ke `fetch failed`, website crawl ke `ENOTFOUND` aur WhatsApp ka slow start hua. `docker-compose.yml` mein teeno services ko `1.1.1.1` aur `8.8.8.8` diye gaye hain (10/10 lookups). Root `.env` mein `DNS_PRIMARY` / `DNS_SECONDARY` se badal sakte hain.

### 14.12 WhatsApp Web ki build pin
OpenWA roz ki sab se nayi WhatsApp Web "alpha" build khud pin karta hai, aur kabhi nayi build engine ko load nahi hone deti (session `initializing` se `disconnected`). Compose mein `WWEBJS_WEB_VERSION` ek chalti hui build par pin hai, aur `WWEBJS_AUTH_TIMEOUT_MS=120000` hai. Agar session phans jaye aur DNS/net theek ho to root `.env` mein `WWEBJS_WEB_VERSION=latest` (ya `off`) try karein. Pin ki hui purani build ko WhatsApp kabhi bhi reject kar sakta hai.

### 14.13 Logo aur rang
Logo `Logo-Concise.png` se bana (`orchestrator/src/dashboard/public/logo.png`, `favicon.png`). Dashboard ka accent brand blue `#004ECB` hai. Logo transparent hai, is liye dark sidebar mein safed card ke andar dikhta hai.

### 14.15 "WhatsApp unreachable" aur `429` (rate limit)
Ye asal mein disconnect nahi tha. OpenWA har route par per-ghanta limit lagata hai (default 1000), aur hamara dashboard har refresh par session ka status OpenWA se poochta tha, to kuch ghanton baad OpenWA `429 Too Many Requests` deta aur dashboard "unreachable" dikha deta. Ab:
- Sirf **watchdog** har 30 second mein OpenWA se status leta hai; dashboard us ka saved copy parhta hai (kitne bhi tab khule hon, OpenWA par bojh nahi).
- Status poochne mein `429` aaye to dashboard purana status "kuch second purana" likh kar dikhata hai, "unreachable" nahi.
- OpenWA ki limits compose mein badhai gayi hain (`RATE_LIMIT_*`), kyunke OpenWA tak sirf hamari app pahunchti hai.
- `429` par app khud 2 se 6 second ruk kar dobara koshish karti hai.
- **"Start / reconnect"** ab har surat mein theek chalta hai: "Session is already started" (400) ko kamyabi samjha jata hai.

### 14.16 Approvals page: refresh aur sending ka waqt
- Pehle poora page har 10 second mein khud badal jata tha (typing aur edits mit jate the). Ab list tab hi badalti hai jab asal mein kuch badla ho, aur agar aap kisi draft mein likh rahe hon (ya unsaved edit ho) to list **nahi chherti**: sirf "Refresh list" ka notice aata hai.
- Sending ka naya panel: "**2 of 5 done · 3 left**", "**Next message in 01:15**" (live countdown), "**About 5m 15s left, done around 19:46**", aakhri bheja hua message, aur **Cancel sending** (baqi messages wapas `pending`; jo ja raha ho wo poora hota hai).
- Har approved draft par "#2 in queue · about 19:42" ka waqt.
- Do messages ke darmiyan gap (60 se 180 second) ban se bachne ke liye hai; Settings, Sending mein badal sakte hain.

### 14.14 Lead kholne par
Lead par click karne par pehle **Details** khulta hai (edit form), phir *Data & research*, *Drafts*, aur **Conversation sab se aakhir mein**.

---

## 15. WhatsApp Status (stories): automatic posting

Dashboard mein naya tab **WhatsApp Status** (`#stories`). Status 24 ghante rehta hai aur sirf wahi log dekhte hain jinhon ne aap ka number save kiya ho. WhatsApp (OpenWA) yeh nahi batata ke kis ne dekha.

**Teen tareeqe:** (1) *From my business data*: AI aap ka company data (AI & Templates tab) aur current offers parh kar 3 ideas deta hai; (2) *My prompt*: aap likhte hain ("30% off offer banao"), preset buttons bhi hain; (3) *My own*: apna text, apni picture, aur neeche website/Instagram/Facebook ki line.

**Image kahan se?** DeepSeek image nahi bana sakta (uska output sirf text hai). Is liye: DeepSeek text likhta hai, aur picture hamara `render.mjs` banata hai (5 designs, aap ka logo aur rang, 1080x1920 PNG, Urdu bhi). Optional AI background `IMAGE_API_KEY` se (default Together AI FLUX.1 schnell, `IMAGE_BASE_URL` / `IMAGE_MODEL` / `IMAGE_COST_USD` badal sakte hain): background mein text nahi hota, text hamesha hum upar likhte hain taake spelling ghalat na ho.

**Autopilot:** Status tab mein on/off, posting times (default 11:00 aur 19:00), roz ki hadd (max 5), *Wait for my approval* (default) ya *Post by itself* (10 minute tak jitter), picture style (card / AI / mixed) aur AI pictures ki roz/mahine ki hadd. AI sirf wahi offer likhta hai jo *Current offers* box mein ya aap ke prompt mein ho; warna evergreen content (services, tip, trust). Pichhle 14 din ke headlines dohrata nahi.

**Safety:** `Dry-run` on ho to kuch post nahi hota; session `ready` hona chahiye; post se pehle `posting` marker likha jata hai, aur agar jawab ka pata na chale (timeout/crash) to khud dobara nahi bheja jata (dobara story lagne se behtar hai ke aap check karein); gateway ka 429 sirf reschedule karta hai; har slot ka marker pehle likha jata hai.

**Engine:** purana OpenWA (whatsapp-web.js) Status aap ki WhatsApp privacy setting ke audience ko bhejta hai. Naya gateway (Baileys) mein audience list zaroori hai: Settings mein `statusRecipients` (max 256 numbers).

**Files:** `src/status.mjs` (engine), `src/render.mjs`, `src/imageapi.mjs`, `src/dashboard/public/stories.js`, tables `status_posts` / `status_usage`. Test (nakli OpenWA/AI/image servers, asli DB nahi chhoota): `docker run --rm --entrypoint node -e OG_STATE_DIR=/tmp/st <image> src/selftest-status.mjs`.

**Image mein naya package:** `@resvg/resvg-js` (picture banane ke liye) aur fonts (DejaVu, Noto Naskh Arabic). Agar install na ho to Status text-status ki tarah post hota hai.

### 15.1 Status v2: Brand kit, link, Campaign Planner, calendar, charts

Status tab ab 4 hisson mein hai: **Create**, **Planner**, **Brand kit & Autopilot**, **Posts**.

- **Free card kaise banta hai:** `src/render.mjs` SVG text banata hai (8 designs) aur `@resvg/resvg-js` (Rust ka renderer, Node addon) usay server par PNG (1080x1920) mein badalta hai. Browser, Chromium ya koi web API nahi chahiye; fonts container ke Linux fonts hain. Text hamesha hum likhte hain, isliye spelling ghalat nahi hoti.
- **Button par link:** WhatsApp Status ki picture par koi tappable jagah nahi hoti, sirf **caption** ka link tap hota hai. Is liye "Button link" (None / Chat on WhatsApp = `wa.me` / apna `https://` link) caption mein `👉 button text: link` ban kar jata hai. Caption 1024 se barhe to link bachta hai, text kat'ta hai.
- **Rang:** Brand kit mein main / dark / text colour aur 6 palettes; har post ka alag rang bhi chun sakte hain. Text ka rang khud chunta hai (contrast 4.5 se upar), jab tak aap apna na den.
- **Picture upload:** apni picture par overlay (kitni dark), text ki position (top/center/bottom), ya "picture only".
- **Autopilot** ab Brand kit istemal karta hai (designs ka set, button text/link, footer), aur uske draft usi editor mein khulte hain.
- **Campaign Planner:** brief + maqsad + 7/14/21/30 din + har 6/8/12/24 ghante (ya din mein 2-3 posts) + posting window. AI khud themes tay karta hai (tips, service, trust, offer, greeting, cta) aur sirf zaroorat par (zyada se zyada 3) sawal poochta hai; phir saari posts 6-6 ki batches mein likhta hai; har post card ban kar **draft** banti hai. Aakhir mein ek sawal: **Approve all** ya badlein. Approve se pehle kuch post nahi hota. Offer sirf "Current offers" box ya aap ke brief se aa sakta hai. Har din 5 posts se zyada nahi, aur ek campaign mein 150 posts tak.
- **Calendar aur charts:** month calendar (har post ka waqt aur state), KPI tiles, posts per day (posted / planned / failed, table view bhi), content mix donut, AI picture ka istemal. Rang `dataviz` checker se light aur dark dono mein verify hue.
- **AI pictures (provider switch):** `.env` mein `IMAGE_PROVIDER=together` (FLUX.1 schnell, ~$0.003 per picture) ya `openai` (gpt-image-1-mini, `IMAGE_QUALITY=low|medium`, ~$0.006 / $0.015 per picture), `IMAGE_API_KEY`, optional `IMAGE_MODEL`, `IMAGE_BASE_URL`, `IMAGE_COST_USD` (kharcha track karne ke liye). Daam blogs aur provider pages se hain: key lene se pehle provider ka page dekhein. Daily/monthly hadd Brand kit & Autopilot tab mein.
- **OpenWA `/chats` par Status kyun nahi dikhta:** OpenWA `status@broadcast` ko Chats se chhupata hai aur apne post kiye status ko store hi nahi karta; uska Status tab sirf doosron ke status dikhata hai. Apna status phone mein *Status / Updates → My status* mein dikhta hai.
