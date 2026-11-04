"""Turn a platform event into webhook deliveries for every matching endpoint of the tenant. The worker sends them."""
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import Instance, WebhookDelivery, WebhookEndpoint

EVENTS = ("message.received", "instance.status")


def make_delivery(db: Session, ep: WebhookEndpoint, event: str, inst: Instance | None, data: dict) -> WebhookDelivery:
    did = str(uuid.uuid4())
    payload = {
        "id": did, "event": event, "createdAt": datetime.now(timezone.utc).isoformat(),
        "instance": {"id": inst.id, "label": inst.label} if inst else None, "data": data,
    }
    d = WebhookDelivery(id=did, tenant_id=ep.tenant_id, endpoint_id=ep.id, event=event, payload=json.dumps(payload, separators=(",", ":")))
    db.add(d)
    return d


def emit(db: Session, tenant_id: str, event: str, inst: Instance | None, data: dict) -> int:
    eps = db.query(WebhookEndpoint).filter(WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.active.is_(True)).all()
    n = 0
    for ep in eps:
        if event in ep.events.split(","):
            make_delivery(db, ep, event, inst, data)
            n += 1
    if n:
        db.commit()
    return n
