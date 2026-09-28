"""AI classification.

Design rule from the project spec: AI never blocks ticket creation. By the
time anything here runs the ticket is already committed, so every failure
path below ends in NEEDS_REVIEW with the ticket intact.
"""

import json
import logging
from dataclasses import dataclass

import anthropic
from supabase import Client

from .config import Settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You triage maintenance and support tickets for a university campus.

Given a ticket, choose the single best department, issue category, and priority
from the lists provided. Respond with JSON only, no prose.

Priority guidance:
- URGENT: safety risk, or a whole building/service is down
- HIGH: one person blocked, or damage will worsen if left
- MEDIUM: normal repair or request
- LOW: cosmetic, or convenience

Confidence is your honest probability that the department is correct, from 0 to 1.
Report low confidence when the ticket is vague. A low score routes the ticket to a
human, which is the correct outcome when you are unsure — do not inflate it."""


@dataclass
class Classification:
    department_id: int | None
    issue_type_id: int | None
    priority_id: int | None
    confidence: float


def _reference_data(admin: Client) -> dict:
    return {
        "departments": (
            admin.table("departments").select("department_id, name, description")
            .eq("active", True).execute().data or []
        ),
        "issue_types": (
            admin.table("issue_types").select("issue_type_id, name, description")
            .execute().data or []
        ),
        "priorities": (
            admin.table("priorities").select("priority_id, name, level")
            .execute().data or []
        ),
    }


def classify(ticket: dict, admin: Client, settings: Settings) -> Classification | None:
    """Return a classification, or None if the classifier is unavailable.

    None is a normal outcome, not an exception. The caller routes to review.
    """
    if not settings.ai_enabled:
        return None

    ref = _reference_data(admin)

    tool = {
        "name": "record_classification",
        "description": "Record the triage decision for this ticket.",
        "input_schema": {
            "type": "object",
            "properties": {
                "department_id": {
                    "type": "integer",
                    "description": "department_id from the provided list",
                },
                "issue_type_id": {
                    "type": "integer",
                    "description": "issue_type_id from the provided list",
                },
                "priority_id": {
                    "type": "integer",
                    "description": "priority_id from the provided list",
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string", "description": "One short sentence."},
            },
            "required": ["department_id", "issue_type_id", "priority_id", "confidence"],
        },
    }

    user_content = json.dumps(
        {
            "ticket": {
                "title": ticket.get("title"),
                "description": ticket.get("description") or ticket.get("original_text"),
            },
            "departments": ref["departments"],
            "issue_types": ref["issue_types"],
            "priorities": ref["priorities"],
        },
        indent=2,
    )

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=20.0)
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={"type": "tool", "name": "record_classification"},
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "route to a human"
        log.warning("Classification call failed for ticket %s: %s", ticket.get("ticket_id"), exc)
        return None

    for block in response.content:
        if block.type == "tool_use":
            data = block.input
            valid_departments = {d["department_id"] for d in ref["departments"]}
            valid_types = {i["issue_type_id"] for i in ref["issue_types"]}
            valid_priorities = {p["priority_id"] for p in ref["priorities"]}

            dept = data.get("department_id")
            itype = data.get("issue_type_id")
            prio = data.get("priority_id")

            # Never trust a generated id. An out-of-range value becomes None,
            # which routes to review rather than writing a bad foreign key.
            return Classification(
                department_id=dept if dept in valid_departments else None,
                issue_type_id=itype if itype in valid_types else None,
                priority_id=prio if prio in valid_priorities else None,
                confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0)))),
            )

    return None
