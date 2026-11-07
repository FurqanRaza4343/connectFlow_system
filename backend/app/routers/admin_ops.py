"""Operations endpoints: platform capacity (admin only) and readiness probe. Wire with `admin_ops.register(app)` in main.py."""
import time
from typing import Any
import json
from datetime import datetime, timezone

import redis
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import wa_client as wa
from ..config import settings
from ..config_ops import ops
from ..db import get_db
from ..deps import Ctx, platform_admin
from ..models import Asset, AssetVariant, Draft, Instance
from ..ops_logging import RequestLogMiddleware, init_sentry, setup_logging
from fastapi import FastAPI

router = APIRouter(tags=["ops"])


def _r() -> Any:
    return redis.Redis.from_url(ops.redis_url, decode_responses=True)


def _json(v: str | None) -> dict:
    try:
        return json.loads(v) if v else {}
    except ValueError:
        return {}


def _storage(db: Session, r: Any) -> dict[str, Any]:
    """Media library figures (counts and bytes only; never file names or content)."""
    from sqlalchemy import func

    now = datetime.now(timezone.utc)
    hour = now.strftime("%Y%m%d%H")
    nbytes = int(db.query(func.coalesce(func.sum(Asset.size_bytes), 0)).scalar() or 0) + int(db.query(func.coalesce(func.sum(AssetVariant.size_bytes), 0)).scalar() or 0)
    return {"backend": settings.storage_backend, "assetsTotal": int(db.query(func.count(Asset.id)).scalar() or 0), "bytesTotal": nbytes,
            "mediaSends1h": int(r.get(f"media:sends:{hour}") or 0), "senderQueueDepth": int(db.query(func.count(Draft.id)).filter(Draft.status == "approved").scalar() or 0),
            "uploadFailures1h": int(r.get(f"asset:fail:{hour}") or 0)}


@router.get("/api/admin/capacity")
async def capacity(db: Session = Depends(get_db), _: Ctx = Depends(platform_admin)) -> dict[str, Any]:
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
        "storage": _storage(db, r),
    }


@router.get("/api/health/ready")
async def ready(db: Session = Depends(get_db)) -> JSONResponse:
    """Readiness: database + redis + gateway, with how long each check took (ms). 503 if any is down
    (used by docker healthchecks and uptime monitors). Nothing secret is ever included."""
    checks: dict[str, str] = {}
    ms: dict[str, float] = {}

    def took(name: str, t0: float) -> None:
        ms[name] = round((time.perf_counter() - t0) * 1000, 1)

    t0 = time.perf_counter()
    try:
        db.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "error"
    took("database", t0)
    t0 = time.perf_counter()
    try:
        checks["redis"] = "ok" if _r().ping() else "error"
    except Exception:  # noqa: BLE001
        checks["redis"] = "error"
    took("redis", t0)
    t0 = time.perf_counter()
    try:
        await wa.health()
        checks["gateway"] = "ok"
    except wa.WAError:
        checks["gateway"] = "error"
    took("gateway", t0)
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"ok": ok, **checks, "latencyMs": ms}, status_code=200 if ok else 503)


def register(app: FastAPI) -> None:
    """Call once from main.py: `admin_ops.register(app)` (JSON logs + request ids + optional Sentry + the routes above)."""
    setup_logging()
    init_sentry()
    app.add_middleware(RequestLogMiddleware)
    app.include_router(router)
