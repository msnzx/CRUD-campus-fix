"""Independent policy adjudication for staff corrections.

Resolves disagreements between prior AI routing and staff operational overrides.
Enforces the mandatory gate: only ADJUDICATED_APPROVED records may enter datasets
or be used for LoRA/distillation training.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .common import (
    DEPARTMENTS, ISSUE_TYPES, ORDINAL, AdjudicationError, GateBlockedError,
    ProvenanceError, digest, require, screen_report_text, token, validate_timestamp
)
from .correction import validate_correction

VERDICTS = {"AFFIRMED_CORRECTION", "AFFIRMED_AI", "REJECTED_BOTH", "POLICY_AMBIGUITY"}


def adjudicate_correction(
    correction: dict[str, Any],
    *,
    adjudicator_id: str,
    verdict: str,
    final_label: dict[str, Any] | None,
    rationale: str,
    approval_ref: str,
    policy_version: str,
    policy_sha256: str,
    adjudicated_at: str,
) -> dict[str, Any]:
    """Execute formal policy adjudication on a staff-correction record."""
    validate_correction(correction)

    if not token(adjudicator_id):
        raise AdjudicationError("Invalid adjudicator_id token")
    # Adjudicator must be independent of the staff corrector
    if adjudicator_id == correction["staff_action"]["staff_user_id"]:
        raise GateBlockedError("Adjudicator must be independent of the staff member who performed the correction")

    if verdict not in VERDICTS:
        raise AdjudicationError(f"Unknown adjudication verdict: {verdict}")

    if not token(approval_ref):
        raise AdjudicationError("Valid approval_ref token is required")
    if not (isinstance(policy_version, str) and policy_version.strip()):
        raise AdjudicationError("policy_version required")
    if not (isinstance(policy_sha256, str) and len(policy_sha256) == 64):
        raise AdjudicationError("policy_sha256 must be a valid 64-char hex digest")

    adj_time = validate_timestamp(adjudicated_at)
    dt_adj = datetime.fromisoformat(adj_time.replace("Z", "+00:00"))
    dt_corr = datetime.fromisoformat(correction["staff_action"]["corrected_at"].replace("Z", "+00:00"))
    if dt_adj < dt_corr:
        raise AdjudicationError("Adjudication timestamp predates the staff correction")

    clean_rationale = screen_report_text(rationale, max_chars=4000)

    # Validate final label according to verdict
    if verdict == "POLICY_AMBIGUITY":
        # Ambiguous cases must NOT carry an authoritative training label
        if final_label is not None:
            raise AdjudicationError("POLICY_AMBIGUITY verdict cannot specify a final training label")
        resolved_label = None
        status = "AMBIGUOUS_EXCLUDED"
    else:
        if not isinstance(final_label, dict):
            raise AdjudicationError(f"Verdict {verdict} requires an explicit final_label dict")
        label_keys = {"department", "issue_type", "priority", "safety_escalation"}
        if set(final_label) != label_keys:
            raise AdjudicationError(f"final_label must have keys {label_keys}")
        if final_label["department"] not in DEPARTMENTS:
            raise AdjudicationError(f"Invalid department in final_label: {final_label['department']}")
        if final_label["issue_type"] not in ISSUE_TYPES:
            raise AdjudicationError(f"Invalid issue_type in final_label: {final_label['issue_type']}")
        if final_label["priority"] not in ORDINAL:
            raise AdjudicationError(f"Invalid priority in final_label: {final_label['priority']}")
        if type(final_label["safety_escalation"]) is not bool:
            raise AdjudicationError("safety_escalation must be a boolean")
        if final_label["safety_escalation"] != (final_label["priority"] == "URGENT"):
            raise AdjudicationError("safety_escalation must strictly match priority == URGENT")

        # Verify verdict semantic alignment
        if verdict == "AFFIRMED_CORRECTION":
            if (final_label["department"] != correction["staff_action"]["department"]
                or final_label["priority"] != correction["staff_action"]["priority"]
                or final_label["issue_type"] != correction["staff_action"]["issue_type"]):
                raise AdjudicationError("AFFIRMED_CORRECTION label must match staff action exactly")
        elif verdict == "AFFIRMED_AI":
            if (final_label["department"] != correction["ai_verdict"]["department"]
                or final_label["priority"] != correction["ai_verdict"]["priority"]
                or final_label["issue_type"] != correction["ai_verdict"]["issue_type"]):
                raise AdjudicationError("AFFIRMED_AI label must match AI verdict exactly")

        resolved_label = {
            "department": final_label["department"],
            "issue_type": final_label["issue_type"],
            "priority": final_label["priority"],
            "safety_escalation": final_label["safety_escalation"],
        }
        status = "ADJUDICATED_APPROVED"

    adjudication_record: dict[str, Any] = {
        "adjudication_id": f"adj-{digest([correction['correction_id'], adjudicator_id, adj_time])[:16]}",
        "correction_id": correction["correction_id"],
        "ticket_id": correction["ticket_id"],
        "correction_provenance_sha256": correction["provenance_sha256"],
        "adjudicator_id": adjudicator_id,
        "verdict": verdict,
        "final_label": resolved_label,
        "rationale": clean_rationale,
        "policy_version": policy_version,
        "policy_sha256": policy_sha256,
        "approval_ref": approval_ref,
        "adjudicated_at": adj_time,
        "status": status,
        "correction_snapshot": correction,
    }
    adjudication_record["adjudication_sha256"] = digest({
        k: v for k, v in adjudication_record.items() if k != "adjudication_sha256"
    })
    return adjudication_record


def filter_approved_adjudications(
    records: list[dict[str, Any]],
    *,
    enforce_gate: bool = True
) -> list[dict[str, Any]]:
    """Strictly filter for ADJUDICATED_APPROVED records.

    Raises GateBlockedError if unadjudicated or ambiguous records are encountered
    when enforce_gate is True.
    """
    approved = []
    for r in records:
        if r.get("status") == "ADJUDICATED_APPROVED" and r.get("final_label") is not None:
            if r.get('adjudication_sha256') != digest({k:v for k,v in r.items() if k!='adjudication_sha256'}):
                raise ProvenanceError('Adjudication commitment mismatch')
            correction=r['correction_snapshot']
            validate_correction(correction)
            if (r['correction_provenance_sha256']!=correction['provenance_sha256']
                or r['correction_id']!=correction['correction_id'] or r['ticket_id']!=correction['ticket_id']):
                raise ProvenanceError('Adjudication source binding mismatch')
            expected=adjudicate_correction(correction,**{key:r[key] for key in (
                'adjudicator_id','verdict','final_label','rationale','approval_ref','policy_version',
                'policy_sha256','adjudicated_at')})
            if expected!=r: raise ProvenanceError('Adjudication semantics mismatch')
            approved.append(r)
        elif enforce_gate:
            raise GateBlockedError(
                f"Record {r.get('correction_id', 'unknown')} has unapproved status: {r.get('status')}. "
                "Unadjudicated or ambiguous corrections must never enter training."
            )
    return approved
