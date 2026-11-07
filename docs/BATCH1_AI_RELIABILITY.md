# Batch 1: AI safety and reliability

Implemented on `layer7-development`, extending the existing Layer 7.1/7.2 jobs,
claims, history, safety intent, and per-recipient notification unique index.
No new framework, paid API, vision integration, model prompt change, or frozen
Layer 5–6 change is included. Nothing was deployed, committed, or pushed.

## Approved safety policy

- `Other + URGENT` remains NEEDS_REVIEW and retains an URGENT suggestion. It
  notifies Campus Safety without treating the review queue as a responsible team.
- If staff changed routing while a valid analysis was running, effective
  department, priority, status, and assignments remain the staff's decision.
  Campus Safety and the current responsible department receive the finding.
- Report edits (including an edit followed by restoring the old text), withdrawn
  tickets, terminal tickets, expired/replaced claims, and explicitly invalidated
  pending analyses do not generate alerts from that result.
- Ordinary emergency submissions retain their insert-time workflow and route.
  If staff later transfer one while analysis is running, the current team's
  unnotified members can receive the valid finding. Existing EMERGENCY pages
  suppress AI pages to the same recipient.
- The unique AI_URGENT index prevents repeat notifications to a person for the
  same ticket. A later valid analysis can notify a new responsible department
  without repeating earlier recipients' pages.

Each new escalation records an analysis UUID (the classification claim token),
contract version `campusfix-text-v2/safety-v1`, and a reason in both durable intent
and SAFETY_ESCALATED history. This version identifies the analysis contract; it
does not claim an immutable Ollama weight digest. Legacy intents are explicitly
labeled `legacy-layer7.2` and retain unknown analysis IDs rather than invented ones.

## Version guards and enrichment

The additive migration adds two trigger-maintained, monotonic ticket counters:
`ai_revision` changes on every ticket update; `ai_report_revision` changes when
original or display report text changes. Callers cannot assign arbitrary counter
values. Claims capture both counters. A content change invalidates the result
before checking whether staff changed routing.

Classification returns a one-use enrichment token and the post-routing ticket
revision. Enrichment writes only through `ai_commit_enrichment`, which locks the
ticket and job, checks that token/revision, rejects withdrawn tickets and newer
analyses, checks staff decision history (including assignment without a ticket
row update), and validates the text field allowlist. Original-title protection is
unchanged. Existing original text cannot be replaced.

An authorized enrichment rewrite carries its own still-pending safety intent to
the derived display revision in the same transaction. It cannot revalidate
another analysis or carry an intent across a staff edit. Notification-creation
failure therefore does not cause a later valid rewrite to lose the safety alert.
Best-effort enrichment is not retried automatically after a process crash.

## Scheduling, retries and crashes

`ai_claim_next_ticket` claims one oldest-due ticket with `FOR UPDATE SKIP LOCKED`.
Workers claim only immediately before execution, so later items in a batch do not
sit on expiring leases. Both PENDING tickets and due FAILED tickets are eligible.
The initial five-second age delay remains.

Automatic failures, stale snapshots, handled exceptions, and expired claims use
the existing attempt counter with `2^attempts` minutes of backoff, capped at 256
minutes. `next_attempt_at = NULL` disables further automatic retries after the
configured limit (default three). Final failures reach FAILED/NEEDS_REVIEW;
emergency effective status and existing staff routing are protected. The worker
reaps expired claims before retrying, so a late old token cannot commit.

Existing manual RPC signatures remain intact. Authorized manual requests retain
the existing cooldown and requester quota and may bypass the automatic backoff;
they do not bypass the attempt limit. Explicit staff reclassification resets
attempts and next-attempt time, invalidates enrichment, and cancels pending safety
intent until a fresh accepted analysis replaces it.

Every blocking classification-worker database call runs in `asyncio.to_thread`.
A bad claim, model call, or commit is isolated to its job; subsequent work and
future ticks continue. Existing SMTP leases and recovery behavior are unchanged.

## Local verification

Use the existing isolated Python 3.12.16 environment with the unchanged runtime
lockfile. Tests mock inference and SMTP; PostgreSQL is an embedded throwaway
server, not a hosted Supabase project:

```powershell
.venv-dbtest312/Scripts/python -m unittest discover -s supabase/tests/local -v
Push-Location backend
../.venv-dbtest312/Scripts/python -m unittest discover -s tests -v
Pop-Location
.venv-dbtest312/Scripts/python evaluation/reproduce_metrics.py --check
.venv-dbtest312/Scripts/python -m unittest discover -s evaluation -v
```

Regression coverage includes raw Other/invalid-category urgency, current-team
paging, report-version invalidation, retained emergency pages, deduplication,
recorded escalation evidence, concurrent enrichment, a real blocking staff
transaction, retry exhaustion, expired-worker fencing, fair queue selection,
worker fault isolation, RPC permissions, and upgrade compatibility.

Final local results (Python 3.12.16, unchanged runtime lockfile):

| Check | Result |
|---|---|
| Backend regression suite | 61 passed, zero skipped |
| PostgreSQL suite, including existing workflow/security checks | 73 passed, zero skipped |
| Frozen evaluation integrity and recorded-metric reproduction | Passed; no inference |
| Offline evaluation suite | 115 passed, three optional skips (118 discovered) |
| Dependency compatibility | All 49 installed runtime/test packages compatible |
| Python compilation and `git diff --check` | Passed |

The optional skips require scikit-learn, Anthropic SDK, and jsonschema. Python
3.11 was not separately executed. No dependencies were installed for this batch.

The local harness still supplies platform shims and enables RLS on existing policy
tables. It does not prove that a clean hosted migration replay independently
establishes all RLS, or exercise PostgREST, real Auth, cron scheduling, SMTP, or
Ollama. Prior full-audit authorization/RLS issues remain release blockers outside
this batch. An unavailable Campus Safety roster retains the existing failed,
retryable intent behavior; no unapproved fallback recipient policy was added.
Legacy intent has no historical input snapshot with which to prove freshness
before the upgrade. Review legacy pending alerts before a future rollout.

SMTP still has an ambiguous-acceptance crash window and is not exactly-once.
Safety notification membership still does not grant cross-department ticket read
access. No live integration, paid inference, or new accuracy evaluation was run.

## Rollback and rollout boundaries

No shared database was changed. Local test databases are disposable and cleaned
up by the harness. No destructive down migration is necessary.

Pre-Batch-1 copies of the seven existing edited files are stored under the
gitignored `.venv-batch1-before/`, with their paths in `manifest.json`. These copies
include earlier uncommitted Layer 7.2 work and are safer than resetting to HEAD.
To undo this batch locally, stop workers/tests, restore those exact saved files,
and remove only these newly added Batch 1 files after review:

- `supabase/migrations/20261106140000_batch1_ai_reliability.sql`
- `supabase/tests/local/test_batch1_db.py`
- `backend/tests/test_batch1.py`
- `docs/BATCH1_AI_RELIABILITY.md`

Keep the earlier Layer 7.2 migrations, SMTP tests, notification code and other
uncommitted work. Do not use `git reset --hard` or a repository-wide clean.

If rollout is approved later, stop old workers before applying the additive
migration and updating code. Old enrichment code has direct service-role writes
and must not run alongside the guarded implementation. If reversal is needed
after a future database rollout, use a separately reviewed forward migration to
restore compatible function bodies while retaining additive columns and data.
