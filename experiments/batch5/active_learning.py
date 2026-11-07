"""Active learning selection engine for staff corrections and candidate tickets.

Implements multi-criteria selection combining uncertainty, disagreement, safety
risk priority, and diversity coverage with near-duplicate suppression.
"""
from __future__ import annotations

from collections import defaultdict
import math
import re
from typing import Any

from .common import (
    ActiveLearningError, DEPARTMENTS, digest, jaccard_word_similarity,
    normalize_text, require
)

HAZARD_KEYWORDS = re.compile(
    r"\b(smoke|fire|spark|sparking|flame|gas|leak|flooding|flood|sewage|"
    r"electrical|shock|hazard|broken glass|collapse|asbestos|chemical|"
    r"lockout|intruder|assault|weapon|injury|emergency)\b",
    re.I
)


def entropy_normalized(probabilities: list[float]) -> float:
    """Compute Shannon entropy normalized to [0, 1]."""
    if not probabilities:
        return 0.0
    k = len(probabilities)
    if k <= 1:
        return 0.0
    entropy = -sum(p * math.log2(p) for p in probabilities if p > 0)
    max_entropy = math.log2(k)
    return max(0.0, min(1.0, entropy / max_entropy))


def margin_score(probabilities: list[float]) -> float:
    """Compute 1 - (p_top1 - p_top2). High score indicates high ambiguity."""
    if len(probabilities) < 2:
        return 0.0
    sorted_p = sorted(probabilities, reverse=True)
    margin = sorted_p[0] - sorted_p[1]
    return max(0.0, min(1.0, 1.0 - margin))


def safety_risk_score(report_text: str, ai_priority: str, staff_priority: str | None = None) -> float:
    """Score potential under-triaged safety risks."""
    has_hazard_term = bool(HAZARD_KEYWORDS.search(report_text))
    score = 0.0
    if has_hazard_term:
        score += 0.4
    # Highest risk: AI predicted non-urgent, but staff upgraded to URGENT
    if staff_priority == "URGENT" and ai_priority != "URGENT":
        score += 0.6
    elif ai_priority != "URGENT" and has_hazard_term:
        score += 0.4
    return min(1.0, score)


def disagreement_score(disagreement_vector: dict[str, Any]) -> float:
    """Calculate operational disagreement magnitude between AI verdict and staff action."""
    score = 0.0
    if disagreement_vector.get("department_changed"):
        score += 0.4
    if disagreement_vector.get("priority_changed"):
        score += 0.35
    if disagreement_vector.get("issue_type_changed"):
        score += 0.25
    return min(1.0, score)


class ActiveLearningSelector:
    """Selects top-K informative candidates with diversity and near-duplicate filtering."""

    def __init__(
        self,
        *,
        weight_uncertainty: float = 0.35,
        weight_disagreement: float = 0.35,
        weight_safety: float = 0.30,
        similarity_threshold: float = 0.85,
        max_per_department: int | None = None,
    ):
        if not math.isclose(weight_uncertainty + weight_disagreement + weight_safety, 1.0, rel_tol=1e-3):
            raise ActiveLearningError("Selection weights must sum to 1.0")
        self.w_unc = weight_uncertainty
        self.w_div = weight_disagreement
        self.w_safe = weight_safety
        self.sim_thresh = similarity_threshold
        self.max_per_dept = max_per_department

    def score_candidate(self, candidate: dict[str, Any]) -> dict[str, float]:
        """Compute composite active-learning priority score for a candidate."""
        # 1. Uncertainty: from confidence or mock probability distribution
        conf = float(candidate.get("ai_verdict", {}).get("confidence", 0.75))
        # Simulated least-confidence score
        s_unc = max(0.0, min(1.0, 1.0 - conf))

        # 2. Disagreement:
        disag = candidate.get("disagreement", {})
        s_div = disagreement_score(disag)

        # 3. Safety:
        report_text = candidate.get("report_text", "")
        ai_prio = candidate.get("ai_verdict", {}).get("priority", "MEDIUM")
        staff_prio = candidate.get("staff_action", {}).get("priority")
        s_safe = safety_risk_score(report_text, ai_prio, staff_prio)

        composite = (self.w_unc * s_unc) + (self.w_div * s_div) + (self.w_safe * s_safe)
        return {
            "composite_score": composite,
            "uncertainty_score": s_unc,
            "disagreement_score": s_div,
            "safety_score": s_safe,
        }

    def select(
        self,
        candidates: list[dict[str, Any]],
        batch_size: int,
    ) -> list[dict[str, Any]]:
        """Select top-K candidates subject to near-duplicate suppression and department quotas."""
        if batch_size <= 0:
            raise ActiveLearningError("batch_size must be positive")
        if not candidates:
            return []

        scored = []
        for c in candidates:
            scores = self.score_candidate(c)
            scored.append({**c, **scores})

        # Sort primarily by composite score descending, tiebreak by ticket_id
        scored.sort(key=lambda x: (-x["composite_score"], x.get("ticket_id", 0)))

        selected: list[dict[str, Any]] = []
        dept_counts: dict[str, int] = defaultdict(int)

        for item in scored:
            if len(selected) >= batch_size:
                break

            target_dept = item.get("staff_action", {}).get("department") or item.get("ai_verdict", {}).get("department", "Other")
            if self.max_per_dept and dept_counts[target_dept] >= self.max_per_dept:
                continue

            # Check near-duplicate similarity against already selected items
            is_dup = False
            item_text = item.get("report_text", "")
            for s in selected:
                sim = jaccard_word_similarity(item_text, s.get("report_text", ""))
                if sim >= self.sim_thresh:
                    is_dup = True
                    break
            if is_dup:
                continue

            dept_counts[target_dept] += 1
            selected.append(item)

        return selected
