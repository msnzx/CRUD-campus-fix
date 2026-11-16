"""CampusFix backend.

Scope is deliberately narrow. The React client talks to Supabase directly for
reads and writes, where RLS enforces authorization. This service exists only
for work that needs a secret or a model call:

  - AI classification and routing
  - email delivery and retry for notification rows (app/notify.py)

Because it holds the service_role key it runs with RLS disabled, so every
endpoint re-checks access explicitly. See app/auth.py.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import Client

from .auth import Caller, admin_client, assert_can_access_ticket, current_caller
from .classify import classify
from .config import Settings, get_settings
from .notify import dispatch_loop, dispatch_pending

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("campusfix")

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Email is a second channel on top of in-app notifications, so a missing
    # SMTP config only means no email, never a failed startup.
    task = asyncio.create_task(dispatch_loop(settings)) if settings.email_enabled else None
    if task is None:
        log.info("Email notifications disabled (SMTP_HOST or service role key not set).")
    yield
    if task:
        task.cancel()


app = FastAPI(title="CampusFix API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Health(BaseModel):
    status: str
    ai_enabled: bool
    admin_enabled: bool
    email_enabled: bool


class ClassifyResult(BaseModel):
    ticket_id: int
    routing_status: str
    department_id: int | None = None
    priority_id: int | None = None
    issue_type_id: int | None = None
    confidence: float | None = None
    detail: str


@app.get("/api/health", response_model=Health)
def health() -> Health:
    return Health(
        status="ok",
        ai_enabled=settings.ai_enabled,
        admin_enabled=settings.admin_enabled,
        email_enabled=settings.email_enabled,
    )


class DispatchResult(BaseModel):
    sent: int
    failed: int
    skipped: int


class InviteUserRequest(BaseModel):
    firstName: str
    lastName: str
    email: str


class PasswordResetRequest(BaseModel):
    email: str


class AdminActionResult(BaseModel):
    message: str


def require_system_admin(caller: Caller) -> None:
    if not caller.is_system_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "System admins only")


@app.post("/api/admin/users/invite", response_model=AdminActionResult)
def invite_user(
    payload: InviteUserRequest,
    caller: Caller = Depends(current_caller),
    admin: Client = Depends(admin_client),
    cfg: Settings = Depends(get_settings),
) -> AdminActionResult:
    """Invite a new account without ever giving an administrator its password."""
    require_system_admin(caller)
    email = payload.email.strip().lower()
    if not email or "@" not in email or not payload.firstName.strip() or not payload.lastName.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a first name, last name, and valid email.")
    try:
        admin.auth.admin.invite_user_by_email(
            email,
            {
                "data": {"first_name": payload.firstName.strip(), "last_name": payload.lastName.strip()},
                "redirect_to": f"{cfg.app_url.rstrip('/')}/reset-password",
            },
        )
    except Exception:
        log.exception("System admin invitation failed")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The invitation could not be sent. Check that this email is not already registered.")
    return AdminActionResult(message=f"An account invitation was sent to {email}.")


@app.post("/api/admin/users/password-reset", response_model=AdminActionResult)
def admin_password_reset(
    payload: PasswordResetRequest,
    caller: Caller = Depends(current_caller),
    admin: Client = Depends(admin_client),
    cfg: Settings = Depends(get_settings),
) -> AdminActionResult:
    """Send a reset link; no admin can view or choose the recipient's password."""
    require_system_admin(caller)
    email = payload.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a valid email.")
    try:
        admin.auth.reset_password_for_email(email, {"redirect_to": f"{cfg.app_url.rstrip('/')}/reset-password"})
    except Exception:
        log.exception("System admin password reset failed")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The reset link could not be sent.")
    return AdminActionResult(message=f"A password reset link was sent to {email}.")


@app.post("/api/notifications/dispatch", response_model=DispatchResult)
def dispatch_notifications(
    caller: Caller = Depends(current_caller),
    admin: Client = Depends(admin_client),
    cfg: Settings = Depends(get_settings),
) -> DispatchResult:
    """Send pending notification emails now instead of waiting for the loop.

    System admins only: it acts on every user's notifications.
    """
    if not caller.is_system_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "System admins only")
    if not cfg.smtp_host:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "SMTP_HOST is not configured.")
    return DispatchResult(**dispatch_pending(admin, cfg))


def _status_id(admin: Client, name: str) -> int | None:
    row = admin.table("statuses").select("status_id").eq("name", name).maybe_single().execute()
    return row.data["status_id"] if row and row.data else None


@app.post("/api/tickets/{ticket_id}/classify", response_model=ClassifyResult)
def classify_ticket(
    ticket_id: int,
    caller: Caller = Depends(current_caller),
    admin: Client = Depends(admin_client),
    cfg: Settings = Depends(get_settings),
) -> ClassifyResult:
    """Classify a ticket and route it.

    Called fire-and-forget by the client right after submission. Every branch
    leaves the ticket in a state a human can act on.
    """
    ticket = assert_can_access_ticket(admin, caller, ticket_id)

    # Only the requester or staff may trigger this, and only once.
    if ticket["ai_routing_status"] not in ("PENDING", "FAILED"):
        return ClassifyResult(
            ticket_id=ticket_id,
            routing_status=ticket["ai_routing_status"],
            detail="Already classified.",
        )

    result = classify(ticket, admin, cfg)

    if result is None:
        # Classifier unavailable. The ticket keeps whatever department its
        # issue type gave it and a human picks up the rest.
        admin.table("tickets").update(
            {"ai_routing_status": "FAILED", "status_id": _status_id(admin, "NEEDS_REVIEW")}
        ).eq("ticket_id", ticket_id).execute()
        return ClassifyResult(
            ticket_id=ticket_id,
            routing_status="FAILED",
            detail="Classifier unavailable; routed for manual review.",
        )

    confident = (
        result.confidence >= cfg.ai_confidence_threshold
        and result.department_id is not None
    )

    update: dict = {
        "ai_suggested_department_id": result.department_id,
        "ai_suggested_issue_type_id": result.issue_type_id,
        "ai_suggested_priority_id": result.priority_id,
        "ai_confidence": round(result.confidence, 4),
    }

    if confident:
        update["ai_routing_status"] = "AUTO_ROUTED"
        update["department_id"] = result.department_id
        update["priority_id"] = result.priority_id
        if result.issue_type_id and not ticket.get("issue_type_id"):
            update["issue_type_id"] = result.issue_type_id
        update["status_id"] = _status_id(admin, "ASSIGNED")
    else:
        update["ai_routing_status"] = "NEEDS_REVIEW"
        update["status_id"] = _status_id(admin, "NEEDS_REVIEW")

    # An emergency ticket is never re-routed away from Campus Safety by the
    # model, regardless of what it suggested.
    if ticket.get("is_emergency"):
        update.pop("department_id", None)
        update.pop("priority_id", None)
        update.pop("status_id", None)

    admin.table("tickets").update(update).eq("ticket_id", ticket_id).execute()

    return ClassifyResult(
        ticket_id=ticket_id,
        routing_status=update["ai_routing_status"],
        department_id=result.department_id,
        priority_id=result.priority_id,
        issue_type_id=result.issue_type_id,
        confidence=round(result.confidence, 4),
        detail="Routed automatically." if confident else "Low confidence; sent for review.",
    )


@app.post("/api/tickets/{ticket_id}/reclassify", response_model=ClassifyResult)
def reclassify_ticket(
    ticket_id: int,
    caller: Caller = Depends(current_caller),
    admin: Client = Depends(admin_client),
    cfg: Settings = Depends(get_settings),
) -> ClassifyResult:
    """Staff-only re-run, for a ticket that failed or was misrouted."""
    if not caller.is_staff:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staff only")

    assert_can_access_ticket(admin, caller, ticket_id)
    admin.table("tickets").update({"ai_routing_status": "PENDING"}).eq(
        "ticket_id", ticket_id
    ).execute()
    return classify_ticket(ticket_id, caller, admin, cfg)
