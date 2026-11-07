# Resumed development audit after Antigravity

Date: 2026-11-07. Branch: `layer7-development`.
Scope: preserve incoming work, verify Batches 1–5, audit/fix unfinished Batch 6,
and execute local regressions. This report records verified results rather than
adopting earlier agents' completion claims. No paid API, deployment, commit,
push, hosted Supabase access or dependency installation was performed.

## Preservation and incoming state

Git initially contained ten modified tracked files and untracked Batch 1–6
packages, tests, SQL and documentation. **84 changed/untracked project files**
were inventoried and copied before editing to:

```text
.venv-resume-before-20261107T051840/
  manifest.json       original file SHA-256 values
  files/              exact original project files
  tracked.patch       original tracked diff, including earlier work
  status.txt          original Git status
```

The snapshot is local and gitignored. Ignored private data, virtual environments
and earlier backups were left in place, not deleted or overwritten. No original
project file was removed. Earlier Layers 5–6 evaluation files and runtime
requirements/lockfile were not edited. Existing Batch 1/2/3/4 implementations,
optional SQL and previous documents were retained; changes below are incremental
fixes rather than replacements of those systems.

`fix_main.py`, `fix_heartbeat.py`, and `tmp_check_imports.py` were inspected and
preserved but **not executed**. The first two perform brittle whole-file text
rewrites; rerunning them can undo reviewed changes. The last imports optional
Ragas as if it were required runtime functionality. It is not a reliable
dependency-integrity check and must not motivate unpinned runtime installs.

## Verified findings and fixes

| Area | Incoming finding | Result |
|---|---|---|
| Observability authentication | The current incoming routes already depended on `current_caller` and checked SYSTEM_ADMIN. There was no verified static-key authentication bypass in that code. Registration instead depended on a raw import-time environment check, bypassing normal Settings handling. | Typed `observability_enabled=false` setting; normal router with feature/config gates, then existing verified JWT and authoritative active-profile role lookup. Disabled or unavailable admin authentication returns 503; missing/invalid sessions return 401 when enabled; non-admin/inactive callers are denied. |
| Heartbeat side effects | Importing health created directories, used a misspelled environment variable and a path relative to the process cwd. | Import performs no filesystem I/O. Explicit export resolves private storage and uses `CAMPUSFIX_PRIVATE`. |
| Heartbeat meaning | A loop pulse was indistinguishable from successful work. SMTP had no pulse; missing backlog appeared to be zero. | Separate monotonic age, outcome, enabled/stale states and process-local scope. SMTP observes progress per delivery slot. Unmeasured backlog is UNKNOWN, not zero. New lifespans reset prior success. Cancellation cannot report success. |
| Concurrent health export | The original fixed temp filename was unsafe; even unique temps raced on Windows replacement. | Unique temp files, fsync, atomic replacement and a local publication lock. Concurrent-export regression passes. |
| Trace context | Child exit replaced the active context with None, losing its parent. Export errors could interfere with cleanup. | ContextVar token restoration; nesting, async siblings and `to_thread` propagation tested. Export/metadata failures preserve application results and original exceptions. |
| Trace privacy/integrity | Regex-only scanning retained raw report/prompt/retrieved text, keyed opaque credentials, mutable buffer aliases and arbitrary event data. | Content fields suppressed; metadata allowlisted at assignment and export; immutable copies, bounded events/buffer, idempotent finish. No document text/URIs, report bodies, tool parameters/results or error prose are captured. |
| Redaction claims | The module claimed guarantees a pattern matcher cannot provide; cycles/unsupported objects were unsafe and one existing redaction marker could hide a second JWT. | Removed the guarantee; bounded JSON-safe recursion and cycle handling; leak assertion checks every known JWT pattern. Redaction alone is not proof of anonymization. |
| External tracing | Arbitrary synchronous HTTP export was described as nonblocking and Phoenix/OTLP compatible without protocol validation. | External exporter fails closed before networking. In-memory diagnostics are explicitly not a validated OTLP/Phoenix wire implementation. |
| Prompts and costs | Registry defaults were invented summaries labeled as actual runtime prompts. Local models accrued cloud-style charges; unknown models got invented rates. Mutable summaries escaped the lock. | No fabricated defaults; exact caller-registered prompt versions are immutable and hashed. Known local API charges are zero, excluding compute/hosting. Unknown prices remain unknown; counters/summaries are thread-safe copies. No cloud rate is guessed. |
| Ragas | `evaluate()` was invoked without explicit model/embedding providers. | Wrapper refuses execution before optional imports/provider construction. No default-provider execution and no fabricated quality scores. |
| Worker logs | Provider exceptions could place private response text in global logs/tracebacks. | Logs retain static messages/error categories without provider bodies. Existing protected ticket-scoped recovery diagnostics remain unchanged; prior regression assertions were not weakened. |
| Batch 5 approval gate | Approval status was trusted without checking adjudication hash, source binding or verdict semantics. | Revalidate the complete record/source and reconstruct adjudication semantics before admission. |
| Batch 5 dataset integrity | Records could be attached to another supplied policy, fixture disabling was ignored, all origins were labeled synthetic, and manifest paths were unrestricted. | Bind the exact policy; enforce fixture-only admission and the fixture flag; derive actual origins; whitelist bundle files and validate resolved paths before reading. Real correction datasets remain blocked. |

The Ragas concern is consistent with its [evaluation API's provider parameters](https://docs.ragas.io/en/stable/references/evaluate/).
Actual OTLP interoperability requires the specified message encoding and
transport, not an arbitrary span dictionary; see the
[OTLP specification](https://opentelemetry.io/docs/specs/otlp/).
Neither integration was executed or enabled here.

## Confirmed Batch 1 queue race

The full PostgreSQL suite reproduced the previously documented intermittent
`KeyError: 'ticket'` in the concurrent oldest-due claim test. Investigation
identified a substantive race, not merely a test inconvenience:

1. The selector's LEFT JOIN candidate snapshot can precede another worker's
   committed claim/backoff update.
2. It then locks the ticket and re-reads the job. The old code treated **any**
   non-null claim token as a crashed lease, even when `claimed_until` was future.
3. A newer backoff with a null token was also ignored before calling the manual
   claim function, which intentionally can bypass automatic backoff.

Two deterministic fault-injection tests pause selection on a job-row lock, update
that row, and resume selection. Against the incoming function they demonstrated
both errors: the live token was cleared, and a backed-off oldest ticket was
claimed instead of the next due ticket. After the fix both pass. The surviving
original lease can still commit its result.

The fix rechecks fresh due time and lease deadline under the existing
ticket → job lock order and retries stale candidates with a 32-iteration bound.
Oldest-due ordering, SKIP LOCKED, crash backoff, attempt limits, staff fencing,
emergency handling and RPC grants remain intact. No retry test was weakened.

Only the existing **uncommitted/local** Batch 1 migration function and its tests
were amended. No shared migration was run. If any environment has already applied
that migration, editing the file does not update its database: a separately
reviewed forward migration is required before any future rollout.

## Files changed during this resume

| Files | Purpose |
|---|---|
| `backend/app/observability/api.py` (new), `config.py`, `main.py` | Typed feature gate, authenticated admin diagnostics and safe worker hooks |
| `backend/app/observability/runtime.py` (new), `notify.py` | Best-effort process progress/tracing, SMTP heartbeat and outcome isolation |
| `backend/app/observability/health.py`, `tracing.py`, `redaction.py` | Side-effect-free/bounded health, concurrency/context/privacy repairs |
| `backend/app/observability/registry.py`, `evaluation.py` | Honest prompt/cost provenance and disabled unconfigured evaluation |
| `backend/app/classify.py`, `enrich.py` | Error-log content suppression only; model prompts, outputs and routing unchanged |
| `experiments/batch5/adjudication.py`, `dataset.py`, `tests/test_batch5.py` | Narrow provenance/admission/path fixes and four additional regressions |
| `supabase/migrations/20261106140000_batch1_ai_reliability.sql`, `supabase/tests/local/test_batch1_db.py` | Fresh-lease/backoff rechecks and two deterministic database regressions |
| `experiments/batch6/dependencies.py`, `experiments/batch6/tests/` (new) | Read-only dependency audit and 34 offline tests |
| `.github/workflows/ci.yml` | Adds Batch 6 offline suite while preserving prior CI steps |
| `docs/RESUMED_DEVELOPMENT_AUDIT_2026-11-07.md` | This report |

## Separate local regression results

Interpreter: **Python 3.12.16**, existing `.venv-dbtest312` environment.
Each row below was executed as a separate command/process. Counts overlap where
component suites are also contained in the complete backend/PostgreSQL suites;
they must not be added together as distinct tests.

| Suite/check | Exact final result |
|---|---|
| Complete backend: `cd backend; python -m unittest discover -s tests -q` | **111 passed**, zero skipped |
| Complete PostgreSQL: `python -m unittest discover -s supabase/tests/local -q` | **113 passed**, zero skipped; previously 111 plus two forced-race tests |
| Batch 2: `python -m unittest discover -s experiments/batch2/tests -q` | **49 passed**, zero skipped |
| Batch 3: backend discovery with `-p test_vision.py` | **26 passed**, zero skipped; native SQL/concurrency coverage also runs in complete PG suite |
| Batch 4: backend discovery with `-p test_knowledge.py` | **24 passed**, zero skipped; native vector/RLS coverage also runs in complete PG suite |
| Batch 4 standalone native retrieval benchmark | **13 queries × four variants**, nine synthetic documents, zero unauthorized source exposures in every variant |
| Batch 5: `python -m unittest discover -s experiments/batch5/tests -q` | **25 passed**, zero skipped; incoming 21 plus four admission/integrity tests |
| Batch 6: `python -m unittest discover -s experiments/batch6/tests -q` | **34 passed**, zero skipped; no incoming Batch 6 regression suite existed |
| Frozen/offline evaluation: `python -m unittest discover -s evaluation -q` | **118 discovered: 115 passed, three optional skips** |
| `evaluation/reproduce_metrics.py --check` | Passed, no new inference |
| `evaluation/dev_audit/analyze_development.py --check` | Passed against available historical development artifacts |
| `python -m experiments.batch6.dependencies` | **42 runtime pins match**, Python/active dependency checks across **50 installed distributions**, zero issues |
| `python -m pip check` | No broken requirements found |
| Compilation and `git diff --check` | Passed |

The benchmark receipt is retained locally at
`.campusfix-private/resume-audit-20261107/batch4-native-benchmark.json`.
Lexical and reranked hybrid fixture recall@5, MRR, nDCG@5 and abstention accuracy
were all 1.0. Vector and unreranked hybrid had the same positive-case ranking
scores but abstention accuracy **8/13**. These easy fixture results do not show
hybrid superiority, semantic generalization or real-world policy accuracy.

Intermediate failures were retained as findings rather than hidden by reruns:
the Windows export race, the queue-race reproductions, and an initially too-broad
change that removed protected recovery-error details expected by existing tests.
Those protected details were restored without changing old assertions. The
final complete suite is green after the actual queue defect was repaired.

The three evaluation skips require optional scikit-learn, Anthropic SDK and
jsonschema. An existing FastAPI/Starlette deprecation warning remains. Optional
`ragas`, `datasets`, `opentelemetry-sdk`, `arize-phoenix` and `llama-index-core`
are absent; no package was installed or downgraded to make tests pass.

PostgreSQL used embedded localhost servers outside the Windows restricted-token
sandbox, with the existing Supabase shims and test RLS-enabling step. Servers
were shut down after testing. These results do not verify hosted Supabase,
PostgREST, production Auth, cron or clean hosted RLS bootstrap.

## Completion assessment and remaining blockers

| Component | Verified local status | Remaining boundary |
|---|---|---|
| Batch 1 / Layers 7.1–7.2 | Regression-tested; confirmed contention defect fixed | No live deployment; SMTP's ambiguous acceptance/crash window still prevents an exactly-once delivery claim |
| Batch 2 | Offline data/evaluation protocol tested | Policy v3 decisions, approved real reports, genuine model scores, calibration and independent model experiments remain pending |
| Batch 3 | Disabled/mock-only production scaffold tested | PNG-only decoder; no paid/live provider, real-photo privacy approval or staff-result UI; optional schema is not a registered rollout migration |
| Batch 4 | Permission-aware synthetic retrieval/native pgvector tested | Hash embeddings and deterministic lexical reranking are fixture components, not trained semantic models; real documents and native LlamaIndex integration are unverified |
| Batch 5 | Synthetic provenance and experiment protocol tested/hardened | CLI adjudications are scripted fixtures, not actual institutional review. Loss curves are deterministic simulations, not training results. Parameter counts use an approximation, not inspected weights/GQA module shapes. Confidence-based selection remains a research heuristic; runtime route flags are not calibrated uncertainty. |
| Batch 6 | Local auth/health/tracing/privacy/integrity defects fixed and tested | Still **PARTIAL** as production LLMOps: no distributed/durable telemetry, actual backlog sampling, complete model/retrieval/manual-request spans, automatic measured token usage/model-weight registry, validated collector transport or real Ragas evaluation |

Observability stays disabled by default. Its endpoints are GET-only system-admin
diagnostics, not new staff permissions. Worker HEALTHY means the last observed
cycle completed; it does not certify alert delivery, backlog emptiness, model
availability or fleet-wide health. `backlog_status=UNKNOWN` remains honest until
an actual measurement is supplied. In-memory spans are capped at 2,000, disappear
on process restart and are not a durable audit log. Concurrent file publishing
is serialized within one process; a multi-process exporter needs separate
coordination and retention design.

Content suppression and static metadata labels reduce telemetry exposure; regex
redaction is not an anonymity proof. Labels must be code-defined references,
not user content. Existing protected database error fields retain scoped detail
for recovery; their retention/access and wider server/dependency logging still
need an explicit production review. Remote collectors and model-based evaluators
remain disabled rather than silently authorizing disclosure or external calls.

Existing global RLS/bootstrap and unassigned-ticket access-policy questions from
earlier audits are not resolved by the local harness. This resume did not invent
new campus safety, privacy, retention or notification policy. No model was
promoted. Python 3.11, remote CI, frontend tests, live Auth/Storage/SMTP/Ollama,
Claude, Phoenix/OTLP and real Ragas/model-quality experiments were not run.

## Recovery and stopping point

The requested local audit, necessary fixes and tests are complete. Stop here;
Batch 6 is not declared production-complete. Before future activation, resolve
the remaining governance/security issues and implement/verify missing features
under an explicit scope.

To reverse only this resume, restore changed existing files from the snapshot's
`files/` copies, consulting its manifest; remove only new files listed above
after review. Do not restore whole files from Git HEAD, reset the branch, clean
the repository or discard earlier Batch 1–5 work. Keep snapshots and private
artifacts until the user chooses their retention. No shared database rollback
is needed because no shared database was changed.
