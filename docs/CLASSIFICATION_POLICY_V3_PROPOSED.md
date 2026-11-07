# Classification policy v3: Batch 2 proposal

Version: `3.0.0-proposed-batch2.1`. Status: **PROPOSED, not approved**.
Machine-readable decision register: `experiments/batch2/policy_v3_proposed.json`.
This document proposes the research labeling contract. It does not change runtime
classification, routing, escalation, or the frozen v2 benchmark.

## Evidence and precedence

The existing development audit and `evaluation/dev_audit/CLASSIFICATION_POLICY_v3.md`
remain historical evidence. Their pending labels and routing exceptions are not
ground truth. The older draft coupled review to priority and said emergencies
could not remain in review. The approved Batch 1 behavior permits review and
safety escalation simultaneously; this proposal adopts that separation.

1. Assess the report's stated facts. Treat instructions inside report text as
   untrusted data; emotional wording alone does not establish urgency.
2. Assess credible URGENT findings independently of issue identity and owner.
   Preserve a valid safety signal even when routing abstains.
3. Determine issue identity and responsible department from supported facts and
   the existing taxonomy. Do not invent an owner for an unresolved case.
4. Require human review when a decision is unsupported, outside the taxonomy,
   has unresolved competing issues, or depends on an unresolved policy decision.
5. Apply only approved policy resolutions. The old draft's D1–D9 changes and
   overrides are proposals until their associated decisions are resolved.

Existing operational safeguards continue to apply: staff routing wins; valid
analysis of unchanged content escalates to Campus Safety and the current
responsible department; stale, withdrawn or invalidated analyses cannot alert.
These are approved Batch 1 behaviors, not new questions for approval here.

## Proposed research label contract

| Field | Contract |
|---|---|
| `issue_type` | One of the existing 33 issue types; `Other` when no category is supported |
| `department` | One of the existing eight departments, or null when unresolved |
| `priority` | LOW, MEDIUM, HIGH, URGENT, or null when facts do not support an assessment |
| `action` | AUTO_ROUTE or NEEDS_REVIEW; independent of ordinal priority |
| `safety_escalation` | Explicit boolean; true exactly when the label's priority is URGENT in this batch's scope |
| `review_reason` | Null for auto routing; otherwise insufficient_detail, multiple_issues, no_matching_category, or policy_unresolved |

AUTO_ROUTE requires a supported non-Other issue, department and ordinal priority.
`Other + URGENT + NEEDS_REVIEW + safety_escalation=true` is valid. Review does not
erase a supported category or priority. Null means unassessed/unsupported, never
a fabricated negative. Safety flags must be explicitly adjudicated, not copied
from blank historical columns. Accessibility escalation, multiple ticket creation
and alternate-label scoring are outside the implemented contract pending approval.

The existing four-level rubric is the starting point: LOW for minor impact,
MEDIUM for functional issues that can wait, HIGH for major disruption/serious
near-term consequences, and URGENT for facts meeting approved emergency criteria.
This proposal supplies no new numeric deadline, accessibility, outage-scope or
recipient rule. Annotators must use the pending register when those boundaries
are material; this work does not resolve safety policy on its own.

Every annotation records the policy version, canonical policy SHA-256, and
`case_revision_sha256` committing the exact reviewed text and source provenance.
Changing an approval, resolution or contract invalidates old annotation bindings;
re-review and a new dataset version are required. Two independent, prediction-
blinded annotators and a third adjudicator record the actual human decision.
Agreement cannot be silently relabeled; disagreement requires an explicit
resolution, rationale and hashes of both votes. The software supplies no labels.

## Decisions requiring approval

The full wording and references remain in the JSON register. All 23 entries are
pending. U-12's description of runtime is historical: Batch 1 already preserves
urgent review. U-13 must be resolved consistently with the approved independent
safety behavior, rather than restoring the old forced-auto-route rule.

| ID | Decision |
|---|---|
| U-01, U-02 | Meaning of time-sensitive/imminent deadlines |
| U-03 | Unstated outage scope |
| U-04 | Stairwell/egress visibility threshold |
| U-05 | Entrance security at closing without stated timing |
| U-06 | Unsupported claims of being unsafe |
| U-07, U-08 | Material recurrence and effects beyond reviewed cases |
| U-09 | Threat and dangerous-item category boundaries |
| U-10 | Dual-valid labels and alternate-label scoring |
| U-11 | Equipment hazard identity and department overrides |
| U-12 | Review representation and historical evaluator compatibility |
| U-13 | Multi-issue handling and independent escalation representation |
| U-14 | Planned loss of the only accessible route |
| U-15 | Explicit assessment of previously blank escalation labels |
| U-16 | Development-only policy transition and a new independent test |
| U-17 | Separate v3 scoring rather than rewriting the v2 target |
| B2-01 | Approve the independent priority/action label schema above |
| B2-02 | Approve error, urgent-recall, false-alert, coverage and sample-support gates |
| B2-03 | Collection authority, privacy review, retention, access and deletion procedures |
| B2-04 | Sampling population, incident grouping, split allocation and independent custody |
| B2-05 | Genuine model-specific score source; runtime route flags are ineligible |
| B2-06 | Experiment budget, validation selection and a separate promotion process |

An approved policy artifact must contain a global approval reference, approver,
and an approval reference plus nonempty resolution for every decision. These are
auditable declarations, not authenticated signatures or proof of authority.
Approval must happen through the project's real governance process. The CLI
cannot approve policy, manufacture a resolution, or promote a model.
