"""Worker loops for WhatsApp Status (registered in worker.py).

  status_loop   : every 60 s, on ONE replica (Redis leader lock): recover stale 'posting' rows, post what is due, autopilot slots, housekeeping.
  campaign_loop : the slow AI steps of campaigns (plan / write). Safe on several replicas: a campaign is claimed with FOR UPDATE SKIP LOCKED."""
import asyncio
import logging

from . import status_campaigns as camp, status_engine as eng
from .locks import leader

log = logging.getLogger("status")


async def status_loop() -> None:
    while True:
        try:
            async with leader("status", 180) as is_leader:   # one replica at a time (a post is never sent twice)
                if is_leader:
                    await eng.status_tick()
                    await asyncio.to_thread(camp.campaign_housekeeping)
        except Exception:  # noqa: BLE001
            log.exception("status loop error")
        await asyncio.sleep(60)


async def campaign_loop() -> None:
    while True:
        try:
            worked = await camp.campaign_step()
        except Exception:  # noqa: BLE001
            log.exception("campaign loop error")
            worked = False
        await asyncio.sleep(0.5 if worked else 3)
