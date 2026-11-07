"""Hourly housekeeping for the media library (runs in the worker):
  * upload reservations that never completed (a crash between 'reserve the quota' and 'store the file') are removed after an hour;
  * files on the local volume that no asset, thumbnail or variant refers to (failed uploads, deleted workspaces) are deleted after an hour.
Cloudinary objects are removed when their asset is deleted; the provider has no cheap 'list everything' call, so there is no sweep for it."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import storage
from .config import settings
from .db import SessionLocal
from .models import Asset, AssetVariant

log = logging.getLogger("assets")
MIN_AGE = 3600


async def sweep_once(db: Session) -> dict[str, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=MIN_AGE)
    stale = db.query(Asset).filter(Asset.kind != "link", Asset.storage_key == "", Asset.created_at < cutoff).delete(synchronize_session=False)
    stale_v = db.query(AssetVariant).filter(AssetVariant.storage_key == "", AssetVariant.created_at < cutoff).delete(synchronize_session=False)
    db.commit()
    orphans = 0
    if settings.storage_backend == "local" or db.query(Asset.id).filter(Asset.storage_backend == "local").first():
        known: set[str] = set()
        for key, thumb in db.query(Asset.storage_key, Asset.thumb_key).filter(Asset.storage_backend == "local").all():
            known.update(k for k in (key, thumb) if k)
        known.update(k for (k,) in db.query(AssetVariant.storage_key).filter(AssetVariant.storage_backend == "local").all() if k)
        orphans = await storage.get_storage("local").sweep_orphans(known, MIN_AGE)
    return {"reservations": int(stale), "variantReservations": int(stale_v), "orphanFiles": orphans}


async def assets_loop() -> None:
    await asyncio.sleep(120)  # let the stack settle after a restart
    while True:
        try:
            with SessionLocal() as db:
                res = await sweep_once(db)
            if any(res.values()):
                log.info("assets sweep: %s", res)
        except Exception:  # noqa: BLE001
            log.exception("assets sweep failed")
        await asyncio.sleep(3600)
