"""Catch-up classification for tickets the client could not hand off.

The classifier may run on a machine that is sometimes off or unreachable. A
ticket submitted in that window keeps ai_routing_status = 'PENDING' because the
browser's request never arrived. When the backend is back, this loop picks
those tickets up and routes them, so nothing waits on someone resubmitting.

Only PENDING tickets are swept. A ticket that already failed is marked FAILED
and left for a human; retrying it here would loop on a down model.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from supabase import create_client

from .config import Settings

log = logging.getLogger("campusfix.sweep")

# Longer than the model timeout, so a request the browser already delivered
# (and abandoned client-side after 8s) is never processed twice.
MIN_AGE = timedelta(seconds=240)
BATCH = 5


def sweep_once(admin, cfg: Settings, route: Callable) -> int:
    cutoff = (datetime.now(timezone.utc) - MIN_AGE).isoformat()
    rows = (
        admin.table("tickets")
        .select("*")
        .eq("ai_routing_status", "PENDING")
        .is_("deleted_at", "null")
        .lt("created_at", cutoff)
        .order("created_at")
        .limit(BATCH)
        .execute()
        .data
        or []
    )
    for ticket in rows:
        try:
            route(ticket, admin, cfg)
        except Exception:  # noqa: BLE001 — one bad ticket must not stop the batch
            log.exception("sweep failed for ticket %s", ticket.get("ticket_id"))
    return len(rows)


async def sweep_loop(cfg: Settings, route: Callable) -> None:
    admin = create_client(cfg.supabase_url, cfg.supabase_service_role_key)
    while True:
        try:
            n = await asyncio.to_thread(sweep_once, admin, cfg, route)
            if n:
                log.info("swept %s pending ticket(s)", n)
        except Exception:  # noqa: BLE001 — the loop must survive a bad pass
            log.exception("sweep pass failed")
        await asyncio.sleep(cfg.sweep_interval_seconds)
