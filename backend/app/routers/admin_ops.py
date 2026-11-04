"""Operations endpoints: platform capacity (admin only) and readiness probe. Wire with `admin_ops.register(app)` in main.py."""
import json
from datetime import datetime, timezone

import redis
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import wa_client as wa
from ..config_ops import ops
from ..db import get_db
from ..deps import Ctx, platform_admin
from ..models import Instance
from ..ops_logging import RequestLogMiddleware, init_sentry, setup_logging

router = APIRouter(tags=["ops"])


def _r() -> redis.Redis:
    return redis.Redis.from_url(ops.redis_url, decode_responses=True)


def _json(v: str | None) -> dict:
    try:
        return json.loads(v) if v else {}
    except ValueError:
        return {}


@router.get("/api/admin/capacity")
async def capacity(db: Session = Depends(get_db), _: Ctx = Depends(platform_admin)):
    # ---- gateway (the gateway's own view; falls back to our database when it is unreachable)
    sessions = ready = qr = 0
    try:
        gw = await wa.list_sessions()
        sessions = len(gw)
        ready = sum(1 for s in gw if s.get("status") == "ready")
        qr = sum(1 for s in gw if s.get("status") == "qr_ready")
    except wa.WAError:
        rows = db.query(Instance.status).all()
        sessions, ready, qr = len(rows), sum(1 for (s,) in rows if s == "ready"), sum(1 for (s,) in rows if s == "qr_ready")
    # ---- database
    size = db.execute(text("select pg_database_size(current_database())")).scalar() or 0
    tables = [{"name": n, "rows": int(c or 0), "bytes": int(b or 0)} for n, c, b in db.execute(text(
        "select relname, n_live_tup, pg_total_relation_size(relid) from pg_stat_user_tables order by pg_total_relation_size(relid) desc"))]
    # ---- ops state kept in Redis by the worker / backup script / security fork
    r = _r()
    ret, bak = _json(r.get("ops:retention:last")), _json(r.get("ops:backup:last"))
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    cap = r.get("ai:global:cap")
    return {
        "gateway": {"sessions": sessions, "maxSessions": ops.gateway_max_sessions or None, "ready": ready, "qrPending": qr, "memMb": None},
        "db": {"sizeBytes": int(size), "tables": tables},
        "retention": {"lastRunAt": ret.get("at"), "deleted": ret.get("deleted", {})},
        "backups": {"lastBackupAt": bak.get("at"), "sizeBytes": bak.get("sizeBytes")},
        "ai": {"callsToday": int(r.get(f"ai:global:{today}") or 0), "globalCap": int(cap) if cap and cap.isdigit() else None, "breakerOpen": bool(r.exists("ai:breaker"))},
        "waitlist": int(r.llen("waitlist:instances")),
    }


@router.get("/api/health/ready")
async def ready(db: Session = Depends(get_db)):
    """Readiness: database + redis + gateway. 503 if any is down (used by docker healthchecks and uptime monitors)."""
    checks: dict[str, str] = {}
    try:
        db.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "error"
    try:
        checks["redis"] = "ok" if _r().ping() else "error"
    except Exception:  # noqa: BLE001
        checks["redis"] = "error"
    try:
        await wa.health()
        checks["gateway"] = "ok"
    except wa.WAError:
        checks["gateway"] = "error"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"ok": ok, **checks}, status_code=200 if ok else 503)


def register(app) -> None:
    """Call once from main.py: `admin_ops.register(app)` (JSON logs + request ids + optional Sentry + the routes above)."""
    setup_logging()
    init_sentry()
    app.add_middleware(RequestLogMiddleware)
    app.include_router(router)
