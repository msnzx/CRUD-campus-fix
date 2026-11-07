# Batch 6 local completion and safety audit

Date: 2026-11-07. Branch: `layer7-development`.

The local monitoring, instrumentation, offline transport tests, deterministic
evaluation boundary and CI changes are ready for code review. This is **not
production approval**. No paid API, Ollama inference, deployment, hosted Supabase
access, dependency installation, commit or push was performed. This report
supersedes the unfinished Batch 6 descriptions in the earlier resumed-development
audit; its historical findings and original backups remain intact.

## Preservation and recovery verification

- Inspected the incoming tracked diff and untracked inventory. The three old
  root-level repair/import scripts were inspected and preserved, not executed.
- Verified every one of the **84 files** in
  `.venv-resume-before-20261107T051840/manifest.json` against its stored SHA-256.
  All 84 passed; all 84 were copied into a temporary directory and compared
  byte-for-byte. None was missing. Sixteen workspace files intentionally differ
  from those historical copies following the previous audit.
- Reconstructed the ten original tracked files from Git HEAD in a temporary
  directory, checked and applied the original `tracked.patch`, and compared the
  recovered files to that snapshot, allowing Git's newline normalization.
  Exact-byte recovery uses `files/`, not the patch.
- Before this task's edits, saved **90 incoming project files** and SHA-256
  values, the tracked binary diff and Git status in
  `.venv-batch6-before-20261107T060302Z/`. Neither backup was overwritten.
- Batch 1 SQL, job controls, safety escalation, SMTP delivery, Batch 2 and Batch 5
  implementations were not changed during this task. Frozen `evaluation/`
  remains unchanged. AST comparisons confirmed the runtime classification and
  enrichment prompts and request payloads are unchanged.
- Private test logs and verification receipts are under `.campusfix-private/`.
  These ignored artifacts and virtual environments must remain private.

## Implemented behavior

### Actual queues and durable worker observations

`monitor.py` samples PostgreSQL aggregates through a service-only, security-invoker
RPC. It reports classification pending/failed/due/backoff/terminal/leased/expired
counts and oldest pending creation time, safety pending/failed/due counts, and
email due/exhausted/ambiguous/leased/expired counts. Classification due respects
the existing five-second age gate. These are observations, not locks or promises
that a worker will claim every counted row.

The sampler writes only its worker observations. It cannot claim jobs, change
tickets, send alerts, or manufacture progress heartbeats. Enabled workers begin
UNKNOWN; disabled, stale, alive, successful and degraded states remain distinct.
PostgreSQL stamps receipt times, preventing future worker wall clocks from
keeping a dead instance apparently fresh. Instances use random UUIDs, not machine
names or user identities. Missing receipts become STALE after 60 seconds.

The API distinguishes database UNKNOWN, FRESH, STALE and UNAVAILABLE. A failed
sample retains its prior observation and age; it never substitutes zero counts.
FRESH describes observation freshness, not successful workers. Worker results
are bounded to 256, with an explicit truncation flag. Process-local health stays
separately labeled. No automatic retention/deletion policy was invented.

Blocking monitoring I/O runs in threads, with transport timeouts and isolated
failures. The monitor retries on subsequent ticks and closes its HTTP client on
shutdown. Configuration stays disabled by default.

The additive schema is pending SQL in
`supabase/optional/batch6_observability.sql`, applied only to disposable test
databases. Supabase CLI is unavailable, so no migration filename was invented.
A CLI-generated forward migration and schema review are required before future
activation. Missing optional SQL makes database monitoring UNAVAILABLE and does
not stop ticket workers.

### AI traces and model observations

Existing classification and enrichment requests now emit metadata-only LLM
spans when observability is enabled. Manual and worker classification attempts
include the protected commit workflow in their parent chain. Existing read-only
tools and their authorized retrieval RPCs produce typed spans. Mock vision spans
are explicitly labeled `MOCK-NO-MODEL-CALL`; no paid vision path was enabled.

Fixed a confirmed defect where privacy filtering converted LLM, RETRIEVER and
TOOL spans into CHAIN spans. Context propagates into worker threads; telemetry
setup/export failures preserve application results and original exceptions.

Exact runtime system instructions are hashed and versioned, with no invented
template defaults. User reports, prompts containing user messages, attachment
bytes, source excerpts/URIs, JWTs and raw outputs/errors are not trace attributes.
Usage comes only from provider token fields. Missing usage is unknown, never
zero; model identities remain provider-reported and weight digests unverified.
Mismatched model identifiers are reduced to a fixed label. A received model
response is not proof that downstream classification validation/commit succeeded.
Known local API charges exclude compute and hosting; unknown prices stay unknown.

### Phoenix-compatible OTLP boundary

Implemented OTLP/HTTP JSON envelope construction, hexadecimal IDs, decimal-string
nanosecond timestamps and integer enum/AnyValue encodings. Export is opt-in and
restricted to numeric HTTP loopback addresses and `/v1/traces`. DNS hosts,
credentials, queries, fragments, remote IPs, proxies and redirects are blocked.

Span completion only queues sanitized metadata. A separate flush sends at most
64 spans from a bounded queue. Retryable failures retain spans with backoff;
permanent failures, overflow and partial rejections have visible counters.
Partial-success responses are not replayed. Responses are read with a size bound.
Raw collector error messages are not retained. Invalid exporter configuration
falls back to memory and exposes a fixed configuration error.

Mock collector tests validate protocol shape, privacy, retries, backoff,
concurrent flush, partial rejection, redirects and malformed/oversized responses.
**No actual Phoenix collector or native OpenTelemetry protobuf parser was run.**
Traces are best-effort process-local diagnostics; crashes can lose queued spans,
and an ambiguous transport acknowledgement can duplicate telemetry. Ticket and
safety delivery durability remains in the existing database jobs/outboxes.

### Ragas-compatible offline evaluation

Added bounded `SingleTurnSample` field mapping and explicit offline exact-match
evaluation. Returned results include actual per-sample scores, aggregation,
sample count, engine/schema identity and zero model calls. Separate ID precision
and recall require caller-supplied relevance IDs; undefined metrics remain null.
No relevance labels, model predictions or quality results are generated.

Default-provider Ragas execution still fails closed. Faithfulness, semantic
similarity and answer relevance are explicitly NOT EVALUATED. Exact match and ID
metrics do not substitute for those judgments. Ragas itself is not installed;
native schema/metric parity and approved local judge experiments remain pending.
Raw sample mapping stays in caller memory; callers must use existing approved
dataset/provenance and private-storage workflows before exporting real evidence.

## Authentication, RLS and sensitive-data findings

- All three diagnostic endpoints (`health`, `traces`, `models`) require the
  feature flag, configured administrative authentication, a verified user token
  and a fresh active SYSTEM_ADMIN database profile. Student/department roles,
  deactivated/revoked roles, missing/invalid tokens and spoofed query parameters
  cannot bypass this boundary. No service-key-as-user shortcut was introduced.
- The new table explicitly enables RLS; anon/authenticated have no privileges
  on it or the aggregate RPC. Even a SYSTEM_ADMIN user cannot call the RPC through
  the user database role. The backend service role is the intended caller.
- Existing knowledge tools retain verified request-local user JWT clients and
  fresh database ACL checks. Instrumentation does not add service-role search.
- Credential scanning covers private keys, supported secret-key formats and
  decoded service-role JWTs, reporting paths/categories rather than values.
  No supported credential patterns were found in the incoming 90-file inventory
  or tracked repository scan. This is not proof that arbitrary sensitive data is
  absent; metadata allowlists remain the trace privacy boundary.
- **Pre-existing deployment blocker:** the local harness enables RLS on older
  policy tables whose migration history omits activation and substitutes platform
  auth/storage/cron shims. Passing these tests is not evidence of live RLS or a
  production bootstrap from migrations alone. Original NULL-department helper
  semantics and deployment-state discrepancies still need a separate reviewed
  remediation. This task did not broaden those permissions.
- Existing `current_caller` performs synchronous Auth/profile reads in an async
  dependency. Its authorization semantics were preserved; API load behavior is
  a remaining general-backend concern. Existing protected ticket-scoped recovery
  error details were preserved and are not exported to global telemetry.

## Separate regression results

Python **3.12.16**, existing `.venv-dbtest312`, existing `requirements.lock.txt`;
no dependency downgrades or installs. Each row is a separate unittest invocation.

| Suite | Discovered | Passed | Skipped | Result |
| --- | ---: | ---: | ---: | --- |
| Complete backend | 111 | 111 | 0 | PASS |
| Complete local PostgreSQL | 121 | 121 | 0 | PASS |
| Batch 2 data/evaluation | 49 | 49 | 0 | PASS |
| Batch 3 vision | 26 | 26 | 0 | PASS |
| Batch 4 knowledge | 24 | 24 | 0 | PASS |
| Batch 5 learning/distillation protocol | 25 | 25 | 0 | PASS |
| Batch 6 security/observability | 55 | 55 | 0 | PASS |
| Frozen evaluation | 118 | 115 | 3 | PASS with optional skips |

Batch 3/4 rows overlap complete backend; do not sum them as unique tests.
The PostgreSQL suite includes eight new monitoring authorization, concurrency,
age-gate, terminal-state and server-clock tests. Batch 6 includes 21 new tests
and extends existing authorization cases to the model endpoint. Two initial
targeted Batch 3/4 invocations from the repository root failed import discovery;
rerunning from the required backend working directory passed without code fixes.

Frozen skips: scikit-learn comparison, Anthropic SDK dependency check and
jsonschema comparison. No paid inference was attempted. Frozen metric reproduction
and historical development-audit reproduction passed using existing outputs;
they are not fresh model benchmarks.

Additional checks: compileall and `git diff --check` passed; `pip check` clean;
installed-metadata audit found **42 matching lock pins, 50 installed distributions,
zero dependency issues**. All pinned distributions advertise compatible Python
3.11/3.12 metadata. Python 3.11 was not available for execution; compatibility is
not runtime-proven there. CI YAML parsed locally, but GitHub Actions/Linux was
not executed. Optional Ragas, datasets, OpenTelemetry SDK, Phoenix and LlamaIndex
packages are absent.

Reproduce using `.venv-dbtest312/Scripts/python.exe` (substitute `python` below):

```text
# backend working directory:
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p test_vision.py -v
python -m unittest discover -s tests -p test_knowledge.py -v
# repository root:
python -m unittest discover -s supabase/tests/local -v
python -m unittest discover -s experiments/batch2/tests -v
python -m unittest discover -s experiments/batch5/tests -v
python -m unittest discover -s experiments/batch6/tests -v
python -m unittest discover -s evaluation -v
python evaluation/reproduce_metrics.py --check
python evaluation/dev_audit/analyze_development.py --check
python -m experiments.batch6.dependencies
python -m experiments.batch6.security_scan
```

The PostgreSQL harness creates and cleans only its temporary local databases.
Windows requires execution outside the restricted process token for native
PostgreSQL; no arbitrary database URL is used. Final logs/receipts are in
`.campusfix-private/suite-*.log` and `batch6-suite-results.json`.

## Files changed during this task

Modified existing incoming files:

- `.github/workflows/ci.yml`: Python 3.11/3.12 matrix, all eight suite categories,
  full lock installation and dependency audit, separate native PostgreSQL job
  refusing skips, least-privilege repository token, credential scanning.
- `backend/app/config.py`, `backend/app/main.py`: disabled-default monitoring
  settings, lifecycle task and manual classification tracing.
- `backend/app/classify.py`, `backend/app/enrich.py`: observe existing requests.
- `backend/app/knowledge/api.py`, `backend/app/knowledge/tools.py`: opt-in,
  failure-isolated tool/retrieval spans.
- `backend/app/vision/worker.py`: opt-in mock-only provider spans.
- `backend/app/observability/api.py`, `runtime.py`, `tracing.py`, `evaluation.py`:
  restricted model endpoint, health projection, typed spans, exporter and metric
  boundaries.
- `experiments/batch6/tests/test_batch6.py`: authorize the new model endpoint.

Added:

- `backend/app/observability/monitor.py`, `models.py`, `otlp.py`.
- `experiments/batch6/security_scan.py`, `tests/test_completion.py`.
- `supabase/optional/batch6_observability.sql`.
- `supabase/tests/local/test_observability_db.py`.
- This report. Private preservation/test receipts are ignored artifacts.

## Remaining blockers and approval boundaries

1. Review and package optional monitoring SQL into a CLI-generated migration;
   separately reconcile the older RLS/bootstrap deployment dependencies. No live
   application is authorized by this task.
2. Approve operational retention/access and capacity budgets before sustained
   telemetry collection. Aggregate scans have not been load-tested on large
   production tables; historical worker receipts currently have no automatic
   deletion. A separate external uptime check is needed if every API instance
   is down; this process cannot report its own total outage.
3. Run native Phoenix/collector and Ragas interoperability in an isolated optional
   environment, and GitHub Actions on Python 3.11/3.12/Linux. No optional SDKs
   were silently added to production dependencies.
4. Approve real datasets/documents, vision privacy policy, unresolved classification
   policy and local judge/model experiments through existing batch gates. No
   model promotion or real-world performance conclusion follows from fixtures.
5. SMTP acceptance/database-commit ambiguity still permits duplicate email after
   certain crashes. Existing retry/ambiguity state remains intact; no exactly-once
   email guarantee is claimed.

## Rollback without losing Batches 1–5

1. Stop local workers and keep `OBSERVABILITY_ENABLED=false` and
   `OBSERVABILITY_EXPORTER=memory`. No existing environment file was edited.
2. Before restoring, preserve any edits made after this report. Compare current
   file hashes against `.campusfix-private/batch6-change-receipt.json`; if a file
   differs, merge or save that later work first.
3. Restore only the modified existing files listed above from the matching paths
   in `.venv-batch6-before-20261107T060302Z/files/`. Those copies include all
   incoming Batch 1–5 and previous audit fixes. Verify each restored SHA-256
   against that snapshot's `manifest.json`. Do not use `git reset`, `git clean`,
   the much older 84-file snapshot, or reverse the entire working-tree diff.
4. Preserve this task's newly added files in a private archive if withdrawing
   them from review. They do not need to be deleted to disable observability.
   No database rollback is needed: SQL was applied only in disposable tests.
   If it is ever applied elsewhere later, disabling monitoring permits leaving
   the additive table/functions intact for a separately reviewed recovery plan.
5. Rerun the separate suites above. Never execute the old root-level repair scripts
   as recovery procedures.

Protocol/security references checked during implementation:
[OTLP specification](https://opentelemetry.io/docs/specs/otlp/),
[Ragas sample schema](https://docs.ragas.io/en/stable/references/evaluation_schema/),
[Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).
