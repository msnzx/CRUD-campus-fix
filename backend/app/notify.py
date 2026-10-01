"""Email delivery for notification rows.

Database triggers write a `notifications` row for every event worth telling
someone about. That row is the in-app notification, and it exists whether or
not email works. This module is the optional second channel: it emails
PENDING rows and records the outcome in `delivery_status`, `attempts`,
`last_attempt_at` and `error_message` (verification.md V-032).

A failed send never touches the ticket. It marks the row FAILED and is retried
with exponential backoff until MAX_ATTEMPTS.
"""

import asyncio
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from supabase import Client, create_client

from .config import Settings

log = logging.getLogger("campusfix.notify")

MAX_ATTEMPTS = 5
BATCH_SIZE = 50


def _due(row: dict, now: datetime) -> bool:
    """PENDING is always due. FAILED waits 2^attempts minutes between tries."""
    if row["delivery_status"] == "PENDING" or not row.get("last_attempt_at"):
        return True
    last = datetime.fromisoformat(row["last_attempt_at"].replace("Z", "+00:00"))
    return now - last >= timedelta(minutes=2 ** row["attempts"])


def _send(cfg: Settings, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = cfg.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if cfg.smtp_user:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.send_message(msg)


def dispatch_pending(admin: Client, cfg: Settings) -> dict:
    """Send one batch. Returns counts, for the admin endpoint and the logs."""
    rows = (
        admin.table("notifications")
        .select("notification_id, ticket_id, message, delivery_status, attempts, "
                "last_attempt_at, users(email, active)")
        .in_("delivery_status", ["PENDING", "FAILED"])
        .lt("attempts", MAX_ATTEMPTS)
        .order("created_at")
        .limit(BATCH_SIZE)
        .execute()
    ).data or []

    now = datetime.now(timezone.utc)
    sent = failed = skipped = 0

    for row in rows:
        if not _due(row, now):
            skipped += 1
            continue

        user = row.get("users") or {}
        update: dict = {"attempts": row["attempts"] + 1, "last_attempt_at": now.isoformat()}
        try:
            if not user.get("active") or not user.get("email"):
                raise RuntimeError("Recipient has no active account")
            link = f"{cfg.app_url}/tickets/{row['ticket_id']}" if row.get("ticket_id") else cfg.app_url
            _send(cfg, user["email"], "CampusFix update", f"{row['message']}\n\n{link}\n")
            update.update(delivery_status="SENT", error_message=None)
            sent += 1
        except Exception as exc:  # noqa: BLE001 — every failure is recorded, none is fatal
            update.update(delivery_status="FAILED", error_message=str(exc)[:500])
            failed += 1
            log.warning("notification %s failed: %s", row["notification_id"], exc)

        admin.table("notifications").update(update).eq(
            "notification_id", row["notification_id"]
        ).execute()

    return {"sent": sent, "failed": failed, "skipped": skipped}


async def dispatch_loop(cfg: Settings) -> None:
    """Background task started with the app when SMTP is configured."""
    admin = create_client(cfg.supabase_url, cfg.supabase_service_role_key)
    while True:
        try:
            result = await asyncio.to_thread(dispatch_pending, admin, cfg)
            if result["sent"] or result["failed"]:
                log.info("notification batch: %s", result)
        except Exception:  # noqa: BLE001 — the loop must survive a bad batch
            log.exception("notification dispatch failed")
        await asyncio.sleep(cfg.notify_interval_seconds)
