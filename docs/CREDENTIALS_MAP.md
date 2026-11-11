# Passwords aur keys: ek hi jagah

**Sab passwords aur keys ek hi file mein hain: `infra/.env`.** Ye file `.gitignore` mein hai (git mein kabhi nahi jati). Isse kisi ke saath share na karein, chat ya screenshot mein na dalein.

Is document mein koi value nahi likhi, isliye ye git mein jana safe hai.

## Kahan login karna hai aur kaunsi line dekhni hai

| Kahan | Address | `infra/.env` ki kaunsi line |
|---|---|---|
| **Puraana dashboard** (Outreach console) | http://127.0.0.1:3100 | `DASHBOARD_PASSWORD` |
| **ConnectFlow admin / customer app** | http://app.connectflow.localhost:8080 (local) | `ADMIN_EMAIL` aur `ADMIN_PASSWORD` |
| **OpenWA purana gateway** (One Group number) | http://127.0.0.1:2785 | `LEGACY_OPENWA_API_KEY` |
| **OpenWA naya gateway** | andar se (containers) | `GATEWAY_OPENWA_API_KEY` |
| **Postgres** | sirf containers ke andar | `POSTGRES_PASSWORD`, `OPENWA_DB_PASSWORD` |

## Baaqi keys

`AI_API_KEY` (DeepSeek, purana dashboard bhi yahi use karta hai), `RESEND_API_KEY` (email), `GOOGLE_CLIENT_SECRET`, `JWT_SECRET`, `LEGACY_SESSION_SECRET`, `LEGACY_WEBHOOK_SECRET`, `OPENWA_WEBHOOK_SECRET` (ye signing ke liye hain, aap type nahi karte). Jab lagayein: `IMAGE_API_KEY`, `STRIPE_*`, `CLOUDINARY_*`.

## Do sachchai jo confuse karti hain

- **OpenWA ki key sirf hash ke taur par store hoti hai.** OpenWA pehli boot par ek random key banata hai aur database mein sirf uska hash rakhta hai, isliye uska asli text wahan se kabhi wapas nahi nikalta. Asli text sirf us ki `.api-key` file mein hota hai, jiski ek **reference copy** ab `infra/.env` mein hai. Agar kabhi OpenWA ki key badli (rotate ki), to `.env` ki copy khud update karni hogi.
- **ConnectFlow admin password sirf pehli dafa istemal hota hai.** Server pehli boot par `ADMIN_PASSWORD` se admin user banata hai aur database mein uska hash rakhta hai. Baad mein `.env` badalne se password nahi badalta (jab tak user app ke andar se badle). Abhi `.env` wali password aur database ka hash **match karte hain** (check kiya gaya).

## Naya password lagane ka tareeqa

1. `infra/.env` mein value badlein.
2. Us service ko dobara chalayein: puraana dashboard `docker compose --profile legacy up -d --force-recreate orchestrator`; ConnectFlow `docker compose up -d --force-recreate api worker`.
3. Purane dashboard ka password badalna chahein to sirf `DASHBOARD_PASSWORD` badalna kaafi hai.

## Yaad rakhne ke liye

`infra/.env` ka ek **backup** kisi password manager (Bitwarden ya KeePassXC, free) mein rakhein. Is file ke purane backups `infra/.env.bak-secrets-<tareekh>` naam se bane hain, wo bhi git-ignored hain; kaam khatam ho to delete kar dein.

## Rotation (11 Nov 2026): kya badla gaya aur kya aap ko badalna hai

**Meri taraf se badal diye gaye (naye values sirf `infra/.env` mein):** `DASHBOARD_PASSWORD`, `ADMIN_PASSWORD` (database ka hash bhi), `JWT_SECRET` (sab ki sessions ek baar logout), `LEGACY_SESSION_SECRET`, `LEGACY_WEBHOOK_SECRET` (purana orchestrator ise har start par OpenWA mein dobara register karta hai). Purani passwords ab 401 deti hain.

**Jo sirf aap badal sakte hain (provider ki website par):**
1. **DeepSeek** (platform.deepseek.com -> API keys): nayi key banayein, purani delete karein, `AI_API_KEY` mein lagayein.
2. **Resend** (resend.com -> API Keys): nayi key, purani delete, `RESEND_API_KEY` mein lagayein.
3. **Google** (agar login istemal ho): `GOOGLE_CLIENT_SECRET` Google Cloud Console mein reset karein.
Phir: `cd infra && docker compose up -d --force-recreate api worker` aur `docker compose --profile legacy up -d --force-recreate orchestrator`.

**Jo jaan-boojh kar nahi badle:** `OPENWA_WEBHOOK_SECRET` (naya gateway har WhatsApp number ke liye ise register karta hai; badalne par un sab ko dobara register karna parega), `POSTGRES_PASSWORD` / `OPENWA_DB_PASSWORD` / `ENCRYPTION_KEY` / `BACKUP_PASSPHRASE` (inhe badalne ke liye database ya purane backups ko bhi chhoona parta hai) aur OpenWA ki admin keys. Ye kabhi chat ya repo mein nahi aayin.
