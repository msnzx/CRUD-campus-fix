"""Staff-correction provenance and divergence tracking.

Captures human operational overrides against prior AI routing results.
Raw corrections are ALWAYS marked PENDING_ADJUDICATION and cannot enter
training datasets without independent policy adjudication.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from typing import Any

from .common import (
    DEPARTMENTS, ISSUE_TYPES, ORDINAL, ROOT, GateBlockedError, ProvenanceError,
    digest, normalize_text, require, screen_report_text, token, validate_timestamp,
    verify_frozen
)

ROLES = {"DEPARTMENT_STAFF", "DEPARTMENT_ADMIN", "SYSTEM_ADMIN"}
ACTION_TYPES = {"TRANSFER", "PRIORITY_OVERRIDE", "RECLASSIFY", "RESOLVE"}
REASONS = {
    "MISCLASSIFIED_DEPARTMENT", "MISCLASSIFIED_ISSUE", "HAZARD_ESCALATION",
    "HAZARD_DOWNGRADE", "ADMINISTRATIVE_LOAD_BALANCE", "POLICY_DISCRETION",
    "INSUFFICIENT_INFORMATION", "OTHER"
}


def build_correction(
    *,
    ticket_id: int,
    ticket_revision: int,
    report_text: str,
    ai_verdict: dict[str, Any],
    staff_action: dict[str, Any],
    origin: str = "synthetic_fixture",
    allow_real_world: bool = False,
) -> dict[str, Any]:
    """Construct an immutable, cryptographically verifiable staff-correction record."""
    if not isinstance(ticket_id, int) or ticket_id <= 0:
        raise ProvenanceError("Invalid ticket_id")
    if not isinstance(ticket_revision, int) or ticket_revision < 0:
        raise ProvenanceError("Invalid ticket_revision")
    if origin != "synthetic_fixture" and not (allow_real_world and origin == "real_world"):
        raise GateBlockedError("Only explicitly marked synthetic fixtures are accepted in this batch")

    # Verify and screen report text
    clean_text = screen_report_text(report_text)
    verify_frozen()
    with (ROOT / "evaluation/campusfix_benchmark_v2.csv").open(encoding="utf-8-sig", newline="") as stream:
        frozen_texts = {normalize_text(row["text"]) for row in csv.DictReader(stream)}
    norm = normalize_text(clean_text)
    if norm in frozen_texts:
        raise GateBlockedError("Report duplicates a frozen benchmark case")
    norm_words = set(norm.split())
    if any(min(len(norm_words), len(set(f.split()))) >= 10
           and len(norm_words & set(f.split())) / len(norm_words | set(f.split())) >= 0.9
           for f in frozen_texts):
        raise GateBlockedError("Report is a near-duplicate of a frozen benchmark case")

    # Validate AI verdict structure
    ai_keys = {"model_id", "analysis_id", "department", "issue_type", "priority", "confidence", "routed_at"}
    if set(ai_verdict) != ai_keys:
        raise ProvenanceError(f"ai_verdict must have exact keys {ai_keys}")
    if ai_verdict["department"] not in DEPARTMENTS:
        raise ProvenanceError(f"Unknown AI department: {ai_verdict['department']}")
    if ai_verdict["issue_type"] not in ISSUE_TYPES:
        raise ProvenanceError(f"Unknown AI issue_type: {ai_verdict['issue_type']}")
    if ai_verdict["priority"] not in ORDINAL:
        raise ProvenanceError(f"Unknown AI priority: {ai_verdict['priority']}")
    if not (isinstance(ai_verdict["confidence"], (int, float)) and 0.0 <= ai_verdict["confidence"] <= 1.0):
        raise ProvenanceError("AI confidence must be in [0.0, 1.0]")
    routed_at = validate_timestamp(ai_verdict["routed_at"])

    # Validate staff action structure
    staff_keys = {
        "staff_user_id", "staff_role", "department", "issue_type",
        "priority", "action_type", "correction_reason", "staff_notes", "corrected_at"
    }
    if set(staff_action) != staff_keys:
        raise ProvenanceError(f"staff_action must have exact keys {staff_keys}")
    if not token(staff_action["staff_user_id"]):
        raise ProvenanceError("Invalid staff_user_id token")
    if staff_action["staff_role"] not in ROLES:
        raise ProvenanceError(f"Unauthorized staff role: {staff_action['staff_role']}")
    if staff_action["department"] not in DEPARTMENTS:
        raise ProvenanceError(f"Unknown staff department: {staff_action['department']}")
    if staff_action["issue_type"] not in ISSUE_TYPES:
        raise ProvenanceError(f"Unknown staff issue_type: {staff_action['issue_type']}")
    if staff_action["priority"] not in ORDINAL:
        raise ProvenanceError(f"Unknown staff priority: {staff_action['priority']}")
    if staff_action["action_type"] not in ACTION_TYPES:
        raise ProvenanceError(f"Unknown action_type: {staff_action['action_type']}")
    if staff_action["correction_reason"] not in REASONS:
        raise ProvenanceError(f"Unknown correction_reason: {staff_action['correction_reason']}")
    corrected_at = validate_timestamp(staff_action["corrected_at"])

    # Chronological fence
    dt_routed = datetime.fromisoformat(routed_at.replace("Z", "+00:00"))
    dt_corrected = datetime.fromisoformat(corrected_at.replace("Z", "+00:00"))
    if dt_corrected < dt_routed:
        raise ProvenanceError("Staff correction predates AI routing event")

    # Evaluate disagreement vector
    dept_changed = ai_verdict["department"] != staff_action["department"]
    prio_changed = ai_verdict["priority"] != staff_action["priority"]
    issue_changed = ai_verdict["issue_type"] != staff_action["issue_type"]

    ai_urgent = ai_verdict["priority"] == "URGENT"
    staff_urgent = staff_action["priority"] == "URGENT"
    if ai_urgent and not staff_urgent:
        urgency_div = "DOWNGRADED_URGENT"
    elif not ai_urgent and staff_urgent:
        urgency_div = "UPGRADED_TO_URGENT"
    elif ai_urgent and staff_urgent:
        urgency_div = "AGREED_URGENT"
    else:
        urgency_div = "RETAINED_NON_URGENT"

    record: dict[str, Any] = {
        "correction_id": f"corr-{digest([ticket_id, ticket_revision, staff_action['staff_user_id'], corrected_at])[:16]}",
        "ticket_id": ticket_id,
        "ticket_revision": ticket_revision,
        "report_text": clean_text,
        "ai_verdict": {
            "model_id": ai_verdict["model_id"],
            "analysis_id": ai_verdict["analysis_id"],
            "department": ai_verdict["department"],
            "issue_type": ai_verdict["issue_type"],
            "priority": ai_verdict["priority"],
            "confidence": float(ai_verdict["confidence"]),
            "routed_at": routed_at,
        },
        "staff_action": {
            "staff_user_id": staff_action["staff_user_id"],
            "staff_role": staff_action["staff_role"],
            "department": staff_action["department"],
            "issue_type": staff_action["issue_type"],
            "priority": staff_action["priority"],
            "action_type": staff_action["action_type"],
            "correction_reason": staff_action["correction_reason"],
            "staff_notes": str(staff_action["staff_notes"] or "").strip(),
            "corrected_at": corrected_at,
        },
        "disagreement": {
            "department_changed": dept_changed,
            "priority_changed": prio_changed,
            "issue_type_changed": issue_changed,
            "any_changed": (dept_changed or prio_changed or issue_changed),
            "urgency_divergence": urgency_div,
        },
        "origin": origin,
        # MANDATORY: Raw corrections can NEVER be directly used as training ground truth.
        "status": "PENDING_ADJUDICATION",
    }
    record["provenance_sha256"] = digest(record)
    return record


def validate_correction(record: dict[str, Any]) -> None:
    """Verify that a correction record has valid schema, provenance hash, and status."""
    required_keys = {
        "correction_id", "ticket_id", "ticket_revision", "report_text",
        "ai_verdict", "staff_action", "disagreement", "origin", "status", "provenance_sha256"
    }
    if set(record) != required_keys:
        raise ProvenanceError("Correction record does not match required schema")
    expected_hash = record["provenance_sha256"]
    unhashed = {k: v for k, v in record.items() if k != "provenance_sha256"}
    if digest(unhashed) != expected_hash:
        raise ProvenanceError("Provenance hash mismatch: correction record has been modified")
    if record["status"] not in ("PENDING_ADJUDICATION", "ADJUDICATED"):
        raise ProvenanceError(f"Invalid correction status: {record['status']}")
