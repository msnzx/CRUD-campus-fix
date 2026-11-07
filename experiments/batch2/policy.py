"""Proposed v3 label structure; no heuristic or model-generated ground truth."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from evaluation.dev_audit.analyze_development import ISSUE_TYPES, DEPARTMENTS, ORDINAL
from .common import digest, read_json, require, token

PROPOSAL = Path(__file__).with_name("policy_v3_proposed.json")
LABEL_KEYS = {"issue_type", "department", "priority", "action", "safety_escalation", "review_reason"}
REVIEW_REASONS = {"insufficient_detail", "multiple_issues", "no_matching_category", "policy_unresolved"}


def proposal() -> dict:
    return deepcopy(read_json(PROPOSAL))


def approved(policy: dict) -> bool:
    expected = proposal()
    return (policy.get("version") == expected["version"]
            and policy.get("contract") == expected["contract"]
            and policy.get('prior_proposal_sha256') == expected['prior_proposal_sha256']
            and policy.get("status") == "APPROVED"
            and token(policy.get("approved_by")) and token(policy.get("approval_ref"))
            and isinstance(policy.get("decisions"), dict)
            and set(policy["decisions"]) == set(expected["decisions"])
            and all(isinstance(d,dict) and d.get('question')==expected['decisions'][key]['question']
                    and d.get("status") == "APPROVED" and token(d.get("approval_ref"))
                    and isinstance(d.get("resolution"), str) and bool(d["resolution"].strip())
                    for key,d in policy["decisions"].items()))


def validate_label(label: dict) -> None:
    require(isinstance(label, dict) and set(label) == LABEL_KEYS, "Label fields do not match proposed v3 contract")
    require(isinstance(label['issue_type'],str) and label["issue_type"] in ISSUE_TYPES, "Unknown issue label")
    require(label["department"] is None or (isinstance(label['department'],str) and label["department"] in DEPARTMENTS), "Unknown department label")
    require(label["priority"] is None or (isinstance(label['priority'],str) and label["priority"] in ORDINAL), "Unknown priority label")
    require(label["action"] in ("AUTO_ROUTE", "NEEDS_REVIEW"), "Unknown action label")
    require(type(label["safety_escalation"]) is bool, "Safety assessment must be explicit")
    require(label["safety_escalation"] == (label["priority"] == "URGENT"), "Safety/urgency assessment is inconsistent")
    if label["action"] == "AUTO_ROUTE":
        require(label["issue_type"] != "Other" and label["department"] is not None
                and label["priority"] is not None and label["review_reason"] is None,
                "Automatic routing requires complete, non-Other labels")
    else:
        require(isinstance(label['review_reason'],str) and label["review_reason"] in REVIEW_REASONS, "Human review requires an explicit reason")


def policy_hash(policy: dict) -> str:
    return digest(policy)
