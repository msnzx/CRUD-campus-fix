"""Request authentication.

The database enforces authorization through RLS for anything the browser does
directly. This service, however, holds the service_role key, which bypasses
RLS entirely — so every endpoint here must establish who is calling and
re-check what they are allowed to touch. Nothing below may assume the
database will catch a mistake.
"""

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from supabase import Client, create_client

from .config import Settings, get_settings

STAFF_ROLES = {"DEPARTMENT_STAFF", "DEPARTMENT_ADMIN", "SYSTEM_ADMIN"}


@dataclass
class Caller:
    user_id: str
    email: str
    role: str
    department_ids: list[int]

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES

    @property
    def is_system_admin(self) -> bool:
        return self.role == "SYSTEM_ADMIN"


def admin_client(settings: Settings = Depends(get_settings)) -> Client:
    """Service-role client. BYPASSES RLS. Never hand this to untrusted input."""
    if not settings.admin_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "SUPABASE_SERVICE_ROLE_KEY is not configured on the server.",
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def current_caller(
    authorization: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> Caller:
    """Resolve the bearer token to a real user, or reject the request."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()

    # Validate the JWT against Supabase Auth rather than decoding it locally.
    anon = create_client(settings.supabase_url, settings.supabase_publishable_key)
    try:
        result = anon.auth.get_user(token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    if not result or not result.user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    user_id = result.user.id

    # Read the profile with the service role. The role name is authoritative
    # from the database — never from a JWT claim the user could influence.
    admin = create_client(settings.supabase_url, settings.supabase_service_role_key)
    profile = (
        admin.table("users")
        .select("user_id, email, active, roles(name)")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )

    if not profile.data or not profile.data.get("active"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No active profile for this user")

    depts = (
        admin.table("user_departments")
        .select("department_id")
        .eq("user_id", user_id)
        .execute()
    )

    role_row = profile.data.get("roles") or {}
    return Caller(
        user_id=user_id,
        email=profile.data.get("email", ""),
        role=role_row.get("name", "STUDENT"),
        department_ids=[d["department_id"] for d in (depts.data or [])],
    )


def assert_can_access_ticket(admin: Client, caller: Caller, ticket_id: int) -> dict:
    """Mirror of private.can_access_ticket(), re-implemented here because the
    service-role client does not run RLS. Returns the ticket row."""
    result = (
        admin.table("tickets")
        .select("*")
        .eq("ticket_id", ticket_id)
        .maybe_single()
        .execute()
    )
    ticket = result.data if result else None
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")

    if caller.is_system_admin:
        return ticket
    if ticket["reported_by"] == caller.user_id:
        return ticket
    if caller.is_staff and ticket.get("department_id") in caller.department_ids:
        return ticket

    # Same response as a missing ticket: do not confirm existence to someone
    # who is not allowed to see it.
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
