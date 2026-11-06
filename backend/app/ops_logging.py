"""Structured JSON logging, request ids and optional Sentry. Never logs secrets, keys, cookies, bodies or query strings."""
from typing import Any
import contextvars
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone

from .config_ops import ops
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
tenant_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("tenant_id", default="")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="")

_EXTRA = ("method", "path", "status", "ms", "event")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        for k, var in (("request_id", request_id_var), ("tenant_id", tenant_id_var), ("user_id", user_id_var)):
            v = var.get()
            if v:
                d[k] = v
        for k in _EXTRA:
            if hasattr(record, k):
                d[k] = getattr(record, k)
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(d, ensure_ascii=False, default=str)


def setup_logging() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter() if ops.log_json else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(h)
    root.setLevel(getattr(logging, ops.log_level.upper(), logging.INFO))
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # httpx logs full URLs; uvicorn.access is replaced by the middleware


def _scrub(event: Any, _hint: Any) -> Any:
    """Sentry before_send: drop everything that could carry a secret or personal data."""
    req = event.get("request") or {}
    for k in ("headers", "cookies", "data", "query_string", "env"):
        req.pop(k, None)
    event["request"] = req
    event.pop("user", None)
    return event


def init_sentry() -> bool:
    if not ops.sentry_dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger("ops").warning("SENTRY_DSN set but sentry-sdk is not installed")
        return False
    sentry_sdk.init(dsn=ops.sentry_dsn, environment=ops.environment, send_default_pii=False, max_request_body_size="never", traces_sample_rate=0.0, before_send=_scrub)
    return True


class RequestLogMiddleware:
    """Pure-ASGI middleware: request id, latency, status. No bodies, headers or query strings are ever read."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.log = logging.getLogger("http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = ""
        for k, v in scope.get("headers", []):
            if k == b"x-request-id":
                rid = v.decode("latin-1")[:64]
        rid = rid if rid.replace("-", "").isalnum() else ""
        rid = rid or uuid.uuid4().hex[:16]
        request_id_var.set(rid)
        tenant_id_var.set("")
        user_id_var.set("")
        t0 = time.perf_counter()
        status = {"v": 500}

        async def send_wrap(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["v"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrap)
        finally:
            path = scope.get("path", "")
            if path not in ("/api/health", "/api/health/ready"):  # probes would drown the log
                self.log.info("request", extra={"method": scope.get("method"), "path": path, "status": status["v"], "ms": round((time.perf_counter() - t0) * 1000, 1)})
