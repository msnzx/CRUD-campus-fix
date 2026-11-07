# Layer 7.2: safety escalation

Batch 1 extends the original policy below: valid URGENT findings can escalate
after staff routing changes, while stale report content cannot. See
[BATCH1_AI_RELIABILITY.md](BATCH1_AI_RELIABILITY.md) for the current versioning,
recipient, recovery, validation, and rollback rules.

This extends the Layer 7.1 guarded classification commit and its unique
`AI_URGENT(ticket_id, user_id)` notification index. It does not add another
classifier, change the prompt, or integrate vision. Layers 5–6 are unchanged.

Accepted text URGENT verdicts, including NEEDS_REVIEW verdicts, notify active
staff/admin members of Campus Safety and the model's responsible department.
Missing or inactive responsible departments leave Campus Safety as the audience.
Overlapping memberships produce one notification per person. Previously flagged
emergencies retain their existing insert-time workflow and routing. Existing
EMERGENCY notifications also suppress AI pages to the same person.

`ticket_safety_escalations` is the durable safety flag and recovery record. One
row per ticket records the first accepted urgent verdict's department. The commit
logs `SAFETY_ESCALATED` once. Ticket locks serialize commits and recovery; the
existing unique index also protects notification inserts. The migration queues
earlier non-emergency URGENT results for recovery, preserving existing AI pages.
Subsequent classifications do not page a completed escalation again.

## Failure and recovery

Escalation `SENT` means the in-app notification rows were created, not that email
was delivered. Email continues to use the existing notification delivery fields
and dispatcher. Creating in-app notifications is atomic across recipients.
Insertion failures leave classification committed and escalation FAILED, with
attempts, last attempt, error, and next attempt recorded. No active Campus Safety
staff is also a detectable failure. Fix team configuration before retrying.

The backend retries due PENDING/FAILED escalations every 30 seconds whenever the
service role is configured, independently of AI and SMTP. Backoff starts at two
minutes and caps at 256 minutes; safety creation retries have no exhaustion limit.
Withdrawn tickets cancel recovery. Worker/RPC connection errors are logged and
retried on the next tick. Database transaction failure leaves either the original
intent or the whole classification uncommitted, so it can be retried safely.

Department staff with ticket access and system admins can inspect escalation
diagnostics. Clients cannot mutate them or invoke recovery RPCs. A backend
operator may retry immediately using the service-role-only RPC:

```sql
select public.escalate_ticket_safety(<ticket_id>);
```

The existing `ticket_ai_jobs.last_error` is the immediate commit diagnostic;
`ticket_safety_escalations` is authoritative for later recovery outcomes. The
classification guard also explicitly rejects null claim tokens and null leases;
a released job cannot commit merely because both stored and supplied tokens are
null.

## SMTP concurrency and crash recovery

`20261106130000_notification_email_leases.sql` extends the existing notification
rows. It does not create another notification stream or change emergency
triggers. It explicitly enables notification RLS; existing personal SELECT and
`read_at` UPDATE permissions remain in force. All four delivery RPCs are callable
only by the service role. The dispatch HTTP endpoint remains System Admin only.

Each dispatcher claims **one** due row immediately before delivery using
`FOR UPDATE SKIP LOCKED`. Claims increment attempts atomically and have a 120-second
lease. A separate, one-use start RPC checks the token and expiry, reads the current
recipient account, and renews the lease. An inactive recipient is recorded as a
failure without sending. Fenced completion rejects replaced tokens; repeating a
completed acknowledgement is idempotent. The backend retries an acknowledgement
three times without repeating SMTP. A QUIT failure after accepted DATA does not
turn a successful send into a retry.

Every notification receives a persisted random UUID used as its stable Message-ID.
This avoids ID collisions between installations and helps compatible mail systems
recognize retries. SMTP does not promise deduplication based on this header.

Expired claims are reaped by the next email dispatch, including a crash on the
final attempt. Crashes before start are recorded separately from an unknown SMTP
outcome after start. Transport loss during the send is conservatively ambiguous;
explicit SMTP rejection is a known failure. Ambiguous attempts remain in
`email_ambiguous_attempts` even if a later retry succeeds. FAILED rows retry with
`2^attempts` minutes of backoff up to five automatic attempts. Exhausted rows stay
FAILED, with an operator-recovery message. In-app alerts remain available.

After fixing SMTP/account configuration and checking mail-server logs, a backend
operator can requeue a FAILED, unclaimed notification:

```sql
select public.email_retry_notification(<notification_id>);
```

This resets the current retry budget, preserves total and ambiguous attempt
counts, and increments `email_manual_retries`. It cannot requeue SENT rows or
steal an outstanding claim. Service operators should monitor:

```sql
select notification_id, delivery_status, attempts, email_total_attempts,
       email_ambiguous_attempts, email_manual_retries, email_claimed_until,
       email_next_attempt_at, error_message
from public.notifications
where delivery_status = 'FAILED'
   or email_claimed_until <= clock_timestamp()
   or email_ambiguous_attempts > 0;
```

SMTP acceptance and database acknowledgement cannot be committed atomically.
A crash after acceptance, a lost acceptance response, or a worker stalled beyond
its lease can still cause duplicate email. Recovery favors retrying an uncertain
alert over silently marking it delivered. This is **not exactly-once email
delivery**, nor a guarantee of eventual delivery after the retry budget is
exhausted. Monitoring and explicit recovery remain operational requirements.

## Local verification and limitations

Run `python -m unittest discover -s supabase/tests/local -v` in the dedicated
database test environment, and `python -m unittest discover -s backend/tests -v`
with the backend importable. The PostgreSQL suite applies all migrations and
includes the existing workflow/security SQL assertions. The embedded harness
uses platform shims and enables existing policy tables' RLS; it does not test
PostgREST, real SMTP, or Supabase platform services.

Validation now uses an isolated **Python 3.12.16** runtime and `.venv-dbtest312`.
All 42 pinned runtime versions in `backend/requirements.lock.txt` match exactly;
`uv pip check` reports all 49 installed runtime/test packages compatible. No
requirements or dependency pins were changed. The earlier Python 3.10 validation
is superseded. Python 3.11 was not separately exercised.

Reproduce on Windows from the repository root:

```powershell
.venv-dbtest312/Scripts/python -m unittest discover -s supabase/tests/local -v
Push-Location backend
../.venv-dbtest312/Scripts/python -m unittest discover -s tests -v
Pop-Location
.venv-dbtest312/Scripts/python evaluation/reproduce_metrics.py --check
.venv-dbtest312/Scripts/python -m unittest discover -s evaluation -v
```

The frozen benchmark integrity check passes. Its offline suite discovers 118
tests: 115 pass and three optional comparisons skip because scikit-learn,
Anthropic SDK, and jsonschema are absent. No live evaluation was run. SMTP tests
use fakes; no external SMTP server, PostgREST, or live Supabase was exercised.
The embedded PostgreSQL tests cover migration upgrade with existing notification
data, real concurrent connections, expired leases, fencing, backoff, exhausted
recovery, RLS/grants, null AI claims, and the existing emergency-to-email flow.
Final results on Python 3.12.16: **56 backend tests passed; 48 PostgreSQL tests
passed**, with no skips in either suite. Compilation and `git diff --check` pass.

Campus Safety notification membership does not grant access to another
department's ticket. Broader Safety read access would require a separate approved
authorization change. Stable per-recipient in-app safety notification uniqueness
is enforced by the existing index; SMTP has the limitations described above.

No paid API calls, live Supabase changes, deployment, commit, or push are part of
this implementation. The local implementation is ready for review with the SMTP
ambiguity and bounded retry policy stated explicitly. Approval of a production
rollout, external SMTP/PostgREST verification, and any broader Campus Safety read
permission remain separate decisions.
