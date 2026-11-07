"""Leased SMTP delivery; ambiguous attempts remain visible and retryable.

Leases prevent ordinary concurrent dispatch. SMTP acceptance and a database
commit cannot be atomic, so a crash can still duplicate email. Stable Message-ID
is a hint for recipients, not an exactly-once guarantee.
"""
import asyncio
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage

from supabase import Client, create_client
from .config import Settings
from .observability.runtime import pulse,outcome,observed_call

log = logging.getLogger("campusfix.notify")
BATCH_SIZE = 50
LEASE_SECONDS = 120


class EmailDeliveryError(RuntimeError):
    def __init__(self, message: str, *, ambiguous: bool):
        super().__init__(message)
        self.ambiguous = ambiguous


def _send(cfg: Settings, to: str, subject: str, body: str, *, message_id: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg.smtp_from, to, subject
    msg["Message-ID"] = message_id
    msg.set_content(body)
    smtp = None
    sending = False
    try:
        smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15)
        smtp.starttls(context=ssl.create_default_context())
        if cfg.smtp_user:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        sending = True
        refused = smtp.send_message(msg)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    except (smtplib.SMTPResponseException, smtplib.SMTPRecipientsRefused) as exc:
        raise EmailDeliveryError(str(exc), ambiguous=False) from exc
    except Exception as exc:
        raise EmailDeliveryError(str(exc), ambiguous=sending) from exc
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                # QUIT failure cannot undo acknowledged DATA acceptance.
                try:
                    smtp.close()
                except Exception:
                    log.warning("SMTP connection cleanup failed")


def _finish(admin: Client, params: dict) -> bool:
    """Retry only the idempotent acknowledgement, never the SMTP send here."""
    for attempt in range(3):
        try:
            return admin.rpc("email_finish_delivery", params).execute().data is True
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.1 * (2 ** attempt))
    return False  # pragma: no cover


def dispatch_pending(admin: Client, cfg: Settings) -> dict:
    """Claim immediately before each send; batch wait cannot expire a lease."""
    sent = failed = skipped = 0
    for _ in range(BATCH_SIZE):
        pulse(cfg,'notifications')
        claim = admin.rpc("email_claim_notification", {
            "p_lease_seconds": LEASE_SECONDS,
        }).execute().data
        if not claim.get("claimed"):
            break
        notification_id = claim["notification_id"]
        identity = {"p_notification_id": notification_id, "p_claim_token": claim["claim_token"]}
        row = admin.rpc("email_start_delivery", identity).execute().data
        if not row.get("started"):
            skipped += 1
            continue
        outcome = {**identity, "p_sent": False, "p_error": None, "p_ambiguous": False}
        try:
            if not row.get("active") or not row.get("email"):
                raise EmailDeliveryError("Recipient has no active account", ambiguous=False)
            link = f"{cfg.app_url}/tickets/{row['ticket_id']}" if row.get("ticket_id") else cfg.app_url
            _send(cfg, row["email"], "CampusFix update", f"{row['message']}\n\n{link}\n",
                  message_id=f"<campusfix-{row['email_message_id']}@notifications.campusfix.invalid>")
            outcome["p_sent"] = True
        except Exception as exc:
            outcome.update(p_error=str(exc)[:500], p_ambiguous=getattr(exc, "ambiguous", True))
            log.warning("notification %s email failed (ambiguous=%s)",
                        notification_id, outcome["p_ambiguous"])
        # Acknowledgement errors must never be interpreted as SMTP failure.
        # Leave the lease recoverable if all acknowledgement attempts fail.
        if not _finish(admin, outcome):
            log.error("notification %s email acknowledgement lost its lease", notification_id)
            skipped += 1
        elif outcome["p_sent"]:
            sent += 1
        else:
            failed += 1
            if row.get("attempts", 0) >= 5:
                log.error("notification %s email attempts exhausted; operator retry required", notification_id)
    return {"sent": sent, "failed": failed, "skipped": skipped}


async def dispatch_loop(cfg: Settings) -> None:
    """Background task started with the app when SMTP is configured."""
    admin = create_client(cfg.supabase_url, cfg.supabase_service_role_key)
    while True:
        pulse(cfg,'notifications')
        try:
            result = await asyncio.to_thread(observed_call,cfg,'notifications',dispatch_pending, admin, cfg)
            outcome(cfg,'notifications',result['failed']==0)
            if result["sent"] or result["failed"]:
                log.info("notification batch: %s", result)
        except asyncio.CancelledError:
            outcome(cfg,'notifications',False)
            raise
        except Exception:
            outcome(cfg,'notifications',False)
            log.error("notification dispatch failed; inspect protected delivery state")
        await asyncio.sleep(cfg.notify_interval_seconds)
