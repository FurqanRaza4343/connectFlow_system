from typing import Any
import asyncio
import time
from datetime import datetime, timezone

from fastapi import FastAPI
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from . import abuse
from .config import settings
from .db import SessionLocal, engine
from .models import Membership, Plan, Tenant, User
from .routers import account, assets, twofactor, admin, admin_ext, admin_ops, admin_security, auth, billing, branding, google, instances, internal, drafts, keys, leads, outreach, team, v1, webhooks
from .security import hash_password
from .telemetry import sweeper, v1_guard
from .tools.reencrypt import backfill

app = FastAPI(title="ConnectFlow API", docs_url="/api/docs", openapi_url="/api/openapi.json")
admin_ops.register(app)
app.include_router(auth.router)
app.include_router(twofactor.router)
app.include_router(account.router)
app.include_router(google.router)
app.include_router(instances.router)
app.include_router(admin.router)
app.include_router(admin_ext.router)
app.include_router(admin_security.router)
app.include_router(internal.router)
app.include_router(keys.router)
app.include_router(v1.router)
app.include_router(webhooks.router)
app.include_router(team.router)
app.include_router(billing.router)
app.include_router(branding.router)
app.include_router(leads.router)
app.include_router(drafts.router)
app.include_router(outreach.router)
app.include_router(assets.router)

app.middleware("http")(v1_guard)  # pre-auth per-IP limiter + 401-storm block for /v1
app.middleware("http")(abuse.trial_guard)  # Trial workspaces: at most 3 new WhatsApp sessions per hour

MB = 1024 * 1024
ASSET_LIMITS = {"trial": (20 * MB, 5), "starter": (200 * MB, 30), "growth": (2048 * MB, 200)}  # media library: (storage bytes, items) per plan
PLANS = [  # (id, name, numbers, messages/day, team members, USD/month, PKR/month, AI calls/day) -- launch estimates, editable in the database
    ("trial", "Trial", 1, 50, 1, 0, 0, 20),
    ("starter", "Starter", 3, 500, 1, 19, 4999, 200),
    ("growth", "Growth", 10, 3000, 10, 59, 14999, 1000),
]


def run_migrations() -> None:
    """Bring the database to the latest Alembic revision. A database created before Alembic existed
    (tables present, no alembic_version) is first stamped as the 0001 baseline."""
    cfg = Config("alembic.ini")
    names = inspect(engine).get_table_names()
    if "users" in names and "alembic_version" not in names:
        command.stamp(cfg, "0001")
    command.upgrade(cfg, "head")


@app.on_event("startup")
def startup() -> None:
    for _ in range(30):  # wait for postgres
        try:
            with engine.connect() as c:
                c.execute(text("select 1"))
            break
        except Exception:
            time.sleep(2)
    run_migrations()
    with SessionLocal() as db:
        backfill(db)  # encrypt any webhook secret that is still plaintext (written before at-rest encryption)
        for pid, name, inst, msgs, members, usd, pkr, ai_cap in PLANS:
            p = db.get(Plan, pid)
            if not p:
                db.add(Plan(id=pid, name=name, max_instances=inst, max_msgs_day=msgs, max_members=members, price_usd=usd, price_pkr=pkr, ai_calls_day=ai_cap,
                            asset_bytes_limit=ASSET_LIMITS[pid][0], asset_count_limit=ASSET_LIMITS[pid][1]))
            elif p.price_usd == 0 and p.price_pkr == 0 and usd:  # first run after the pricing columns were added
                p.max_members, p.price_usd, p.price_pkr = members, usd, pkr
        if settings.admin_email and settings.admin_password and not db.query(User).filter(User.email == settings.admin_email.lower()).first():
            admin_user = User(email=settings.admin_email.lower(), name="Platform admin", password_hash=hash_password(settings.admin_password), is_platform_admin=True, email_verified_at=datetime.now(timezone.utc))
            ws = Tenant(name="ConnectFlow", plan_id="growth")
            db.add_all([admin_user, ws])
            db.flush()
            db.add(Membership(user_id=admin_user.id, tenant_id=ws.id, role="owner"))
        db.commit()


_bg: list = []


@app.on_event("startup")
async def start_telemetry() -> None:
    _bg.append(asyncio.create_task(sweeper()))  # flushes key-usage counters to Postgres every 60 s


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True}
