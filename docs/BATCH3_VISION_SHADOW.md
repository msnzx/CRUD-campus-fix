# Batch 3: disabled Claude Vision shadow infrastructure

Date: 2026-11-06. Branch: `layer7-development`.

**Scope delivered:** production-package attachment validation, Claude-style
structured request/response adapter, observational fusion, durable shadow jobs
and results, bounded retries and offline integration tests. The application is
disabled by default. Setting `VISION_MODE=shadow` reports **POLICY_BLOCKED**;
production registers no provider or vision worker. Paid execution cannot be
enabled by a flag, API key or HTTP request. No Anthropic SDK, live transport,
approved model, paid health probe, photo export or automatic vision routing was
added. The complete execution path is exercised with explicitly injected mock
providers and temporary fixture images only.

This is infrastructure for review, not an enabled or accuracy-validated Claude
integration. Work stops at real-photo processing, external disclosure and safety
activation until the decisions below are approved. Existing text-based safety
escalation continues unchanged.

## Inspection and reuse

The existing attachment flow stores a private object under
`<ticket_id>/<uuid>.<extension>`, then inserts an attachment row after ticket
creation. No upload-complete event exists. Bucket MIME and size checks trust
declared metadata; the bucket also accepts HEIC and larger files than this stage.
An attachment row alone is not authorization to fetch an arbitrary URL.

Layer 6 already supplies a frozen output schema, prompt and semantic validator.
Reviewed copies live in `backend/app/vision/`; tests check byte equality for the
base schema/prompt and normalized equality for the validator (only its schema
filename changes). The production package never imports the evaluation harness.
The derived **shadow 1.1** schema adds `multiple_issues_detected`, including
multiple issues within a single image, without changing the frozen 1.0 schema.
The derived prompt/schema are unmeasured and not promoted for live use.

Classification leases remain owned by `ticket_ai_jobs`. Vision has independent
stage state using the same established claim/lease/fenced-commit pattern; it
cannot consume a classification lease. No new orchestration framework is used.
There are no changes to frozen Layers 5–6, existing Layer 7 SQL, routing,
enrichment, notification code, staff access rules or emergency behavior.

## Files changed for this batch

| File | Purpose |
|---|---|
| `backend/app/config.py` | Disabled/shadow enum; paid-call property always false |
| `backend/app/main.py` | Honest health fields and a blocked-shadow startup message; no vision worker registered |
| `backend/app/vision/attachments.py` | Authorized-path validation, bounded private storage streaming and static-PNG sanitizer |
| `backend/app/vision/contract.py` | Derived structured contract, bounded response interpretation, minimization, provenance and pure fusion |
| `backend/app/vision/worker.py` | Mock-provider gate, off-event-loop execution, discovery/claims, deadline fencing and failure isolation |
| `backend/app/vision/validation.py` | Reviewed Layer 6 validator copy with a local schema path |
| `backend/app/vision/prompts_v1.py` | Unchanged frozen prompt copy |
| `backend/app/vision/schema/layer6_v1.schema.json` | Unchanged frozen schema copy |
| `backend/app/vision/__init__.py` | Production package boundary |
| `backend/tests/test_vision.py` | Mock/security/failure regression tests, automatically discovered by existing backend CI |
| `supabase/optional/batch3_vision_shadow.sql` | Pending additive service-only tables and RPCs; applied only inside local tests |
| `supabase/tests/local/test_vision_db.py` | Real PostgreSQL concurrency, authorization, rollback and worker integration tests |
| `docs/BATCH3_VISION_SHADOW.md` | This operating/limitations report |

Other modified/untracked backend, migration, test, Batch 2 and documentation
files were already present when this batch began and were preserved. No new
dependencies or lockfile edits were needed.

## Attachment security and supported formats

1. The backend uses the database-issued attachment snapshot for the claimed
   ticket. Before any fetch, it checks a strict ticket/UUID/extension path,
   matching ticket ID, positive attachment ID, declared type/size, existing
   storage object and matching uploader/object owner. Cross-ticket paths,
   URLs, traversal, encoded separators, query strings and malformed paths fail
   closed. Rejected attachments do not trigger partial-photo inference.
2. Storage reads use the configured trusted Supabase origin and fixed private
   bucket/path. Credentials stay on that origin; redirects and environment
   proxies are disabled. No signed/public object URL reaches a provider. HTTPS
   is required except localhost for tests. The service-role storage access model
   is consistent with the [Supabase storage access documentation](https://supabase.com/docs/guides/storage/security/access-control).
3. Reads stream with bounded bytes, a short connect/read timeout and an elapsed
   read budget. Header size/type and actual bytes must agree with metadata.
   Encoded responses are rejected. No automatic decompression or unbounded SDK
   `download()` is used. Transient 404/429/5xx and network errors are retryable;
   redirects, authentication errors, type/size violations are terminal review.
4. The current decoder accepts **static, noninterlaced, 8-bit grayscale/RGB,
   with or without alpha PNG**. It checks signatures, chunk boundaries/order,
   CRCs, dimensions, bounded deflate output, scanline lengths/filter bytes and
   an exact end marker; trailing data, animation and unsupported critical chunks
   are rejected. It reconstructs a PNG containing only validated IHDR, IDAT and
   IEND, dropping OCR/EXIF/text and other ancillary chunks. It does not infer or
   blur faces/plates. Dropping orientation/color metadata can affect appearance;
   this must be evaluated before any real inference.
5. Resource caps are four images, 7,500,000 raw/sanitized bytes per image,
   12,000,000 aggregate raw/sanitized bytes, 8,000 pixels per side and 8,000,000
   pixels per image. These are conservative local resource limits, not a claim
   about current provider limits. They apply before provider dispatch.

**JPEG, WebP, GIF, HEIC, palette/other PNG encodings and oversized batches are not
decoded by this implementation.** They receive explicit review codes. Pillow is
absent from the locked environment; header sniffing alone was not substituted
for validation. Broader formats require a reviewed, pinned decoder dependency,
metadata handling, resource caps and additional tests. No uploader/UI/bucket
restriction was changed; attachments remain available through existing flows.

## Structured understanding and fusion

The adapter builds base64 image content, the existing report-as-untrusted-data
prompt and a strict JSON-schema output request. No tools are exposed. The model
identifier is explicitly `UNAPPROVED-CLAUDE-MODEL`; it is a mock protocol marker,
not an invented deployed model or evidence of a current Claude release.

Interpretation rejects model mismatch, refusal, truncation, unexpected stop,
multiple/invalid text blocks, oversized/deep output, duplicate JSON keys,
non-finite values, schema violations, unsupported usage metadata and semantic
inconsistency. Image references must cover the supplied images exactly. Multiple
issues require abstention; conflicting photos also abstain. Visible hazards
remain explicit even during valid abstention. Confidence remains a categorical,
unvalidated model report, never a calibrated probability.

Only bounded enums, booleans, image-quality categories, image indices, hashes,
usage counts and timing survive interpretation. OCR, narrative summaries,
evidence descriptions, uncertainty prose and raw responses are discarded. The
request body and report text are never written to vision result/log fields.

Fusion records agreement/disagreement/no text baseline, review reasons, existing
text urgency and observed vision hazards. It always records human review,
`routing_applied=false` and `notifications_created=false`. A clear photo, no
visible issue, disagreement, low confidence, multiple issues, refusal or failure
cannot overwrite a text result, negate text urgency, transfer a ticket, change
status/priority, invalidate staff authority, create another ticket, or page
anyone. Recognition behavior is tested only on scripted mock outputs; actual
Claude detection quality is **unknown**.

## Durable jobs, versions and recovery

The pending SQL adds two RLS-enabled public tables with all public/anon/
authenticated privileges revoked. Only service-role access is granted; even a
signed-in system admin has no direct access. Three service-only invoker RPCs
discover, claim and finish jobs. The private snapshot helper has an empty search
path and restricted execute permissions. There are no security-definer bypasses,
new client endpoints, ticket triggers, user grants or production cron jobs.

Discovery scans ticket IDs in bounded cursor pages and wraps so late attachments
on old tickets are revisited. Existing jobs are scanned even after their last
attachment is removed or the ticket is withdrawn. A snapshot commits the report
revision, text-analysis ID/suggestions, ordered attachment metadata, uploader and
object identity/version fingerprint. It excludes raw report text and object
metadata values. A new snapshot increments the generation, fences an old token
and schedules new work. Storage replacement is detected when its identity,
version, metadata or timestamp changes; raw and sanitized content hashes also
identify what was actually processed. Undetectable out-of-band byte replacement
that preserves all metadata is not solved; normal authenticated clients have
no storage-update policy. Future storage integration must verify real version/
ETag behavior rather than assume the local shim proves it.

Discovery returns at most five attachment descriptors plus the total count. Five
is an explicit overflow/review sentinel, never a partial four-photo analysis;
larger batches cannot produce findings. Overlong path/type metadata is replaced
with an invalid null marker, never truncated into a potentially valid path.

Claims lock ticket → job → run consistently, skip locked work and create an
attempt row/token atomically. Each generation has at most three claims. Finish
compares the current token, lease deadline, withdrawal and complete current
snapshot before accepting findings. Replayed finishes do not duplicate results;
response loss after commit leaves a single completed run. A rolled-back commit
leaves the original lease/attempt recoverable.

Handled transient failures back off for `2^attempts` seconds (2, then 4 seconds)
before another claim; the third failure ends in REVIEW. Expired processing leases
record CRASHED/LEASE_EXPIRED and use the same bounded backoff. A worker deadline
is 60 seconds by default, below its 120-second lease; timeout records a retry and
clears/fences that token. Python cannot kill a running thread, so it may finish
later, but its late commit is rejected. Durable lease expiry covers failure of
the timeout/retry write itself. There is no guarantee of exactly-once provider
execution after a crash; repeated **mock** execution can occur. No paid transport
exists in this batch.

Blocking database, storage and mock analysis run off the asyncio event loop.
Tick failures do not kill subsequent ticks and log only a constant warning.
Provenance includes pipeline/prompt/schema/code hashes, input snapshot hash,
report/ticket revision, attempt/generation, worker, MOCK provider/model marker,
image hashes/sanitizer version, response hash, token usage and timing as available.
Errors store fixed codes, not provider messages. A pipeline/prompt/schema upgrade
must bump the pipeline version in both Python and SQL before reprocessing.

Old successful runs remain historical. A current result must match the job's
current generation and terminal successful state; never select the newest
successful run while ignoring an in-progress newer generation or withdrawal.
There is no staff-facing result API/UI in this batch. REVIEW is a durable
**service-only diagnostic fallback**, not a new staff notification or a change
to the ticket's existing actionable queue. Result visibility and a live review
workflow await policy approval.
SUCCEEDED means a validated mock result was durably accepted; it does not mean
the model was correct, the report was resolved, or a human reviewed it.

## Pending SQL and local execution

No migration or SQL was applied to a shared database. The Supabase CLI and npx
are unavailable in this environment. The repository's
[Supabase skill](../.agents/skills/supabase/SKILL.md) explicitly requires:
“In imperative migration projects, create new hand-authored migration files with
`supabase migration new <name>` first. Never invent a migration filename”.
Accordingly the executable schema is retained in `supabase/optional/`, outside
automatic migrations. The database tests apply every existing migration, then
this file, exclusively to a disposable local PostgreSQL instance. A future
approved rollout must generate/review a proper migration with the CLI; it must
not assume this file was installed by the migration runner. CLI advisors and
hosted/PostgREST checks could not be run. The disabled application does not
depend on these tables being installed.

Offline commands, using the existing Python 3.12 environment:

```powershell
Push-Location backend
../.venv-dbtest312/Scripts/python.exe -m unittest discover -s tests -q
Pop-Location
.venv-dbtest312/Scripts/python.exe -m unittest discover -s supabase/tests/local -q
.venv-dbtest312/Scripts/python.exe -m unittest discover -s experiments/batch2/tests -q
.venv-dbtest312/Scripts/python.exe -m unittest discover -s evaluation -q
.venv-dbtest312/Scripts/python.exe evaluation/reproduce_metrics.py --check
```

On this Windows host, local PostgreSQL must run outside the restricted-token
sandbox. The harness supplies Supabase platform shims and enables RLS for
existing policy tables. New vision RLS is enabled explicitly by the pending SQL.
Storage fixture creation uses the local database owner; the production service
role receives SELECT only on storage objects. The reduced shim does not verify
real storage HTTP, Auth, PostgREST, metadata/version behavior or hosted permissions.

## Decisions and limitations before further activation

Work stops here pending approval for:

1. **Photo privacy and external disclosure:** authority/consent, sensitive report
   handling, faces/plates/location data, redaction and provider processing terms.
   Static-PNG metadata stripping does not anonymize visible content.
2. **Retention/access/deletion:** who may see derived findings and diagnostics,
   retention of hashes/manifests/results, audit access and deletion/withdrawal
   handling. No real photos or reports were collected or retained by these tests.
3. **Vision hazard and disagreement policy:** whether an observed hazard should
   page anyone, how it interacts with text urgency/staff decisions, and the actual
   human-review workflow. This batch does not reuse text-alert authority to infer
   permission for vision alerts, nor invent a new safety threshold.
4. **Model, spend and evidence:** approved provider/model/version, real annotated
   photos, live evaluation, budget/retry/circuit-breaker policy and shadow-review
   acceptance criteria. No live transport, SDK dependency, budget mechanism,
   real-photo worker registration or model promotion is enabled here. A future
   implementation must add those under separate approval; merely setting a key
   or `VISION_MODE=shadow` is insufficient.

Automatic vision routing remains prohibited. No paid API, Ollama, deployment,
commit or push occurred. Python 3.11, remote CI, frontend tests, hosted database,
live storage/Claude/SMTP, real-photo privacy redaction and actual vision accuracy
were not tested. Optional evaluation dependency skips remain documented in the
test results. No dependency was installed or downgraded.

## Rollback

No shared schema rollback is needed. Keep `VISION_MODE=disabled`. To undo this
batch locally, remove the new vision package, its backend/database tests, this
document and the pending optional SQL; remove only the added vision settings,
health fields and startup message from `config.py`/`main.py`. Preserve earlier
Batch 1/2 and Layer 7 edits. Do not reset the branch, restore whole files from
HEAD, remove earlier migrations or run a broad clean. Temporary PostgreSQL test
databases are shut down and disposed of by the harness.

## Final local results

Executed with Python **3.12.16** in the existing locked environment:

| Check | Result |
|---|---|
| Backend suite | **87 passed**, zero skipped (61 existing + 26 new vision tests) |
| Complete local PostgreSQL suite, final run | **92 passed**, zero skipped (73 existing + 19 new vision tests) |
| Batch 2 offline protocol | **49 passed**, zero skipped |
| Frozen/offline evaluation | **118 discovered: 115 passed, three optional skips** |
| Frozen benchmark integrity and recorded metric reproduction | Passed, no inference |
| Python compilation and `git diff --check` | Passed |
| Frozen files and runtime requirements/lockfile | No Batch 3 edits |

The three evaluation skips require optional scikit-learn, Anthropic SDK and
jsonschema packages. They were not installed. The existing FastAPI/Starlette
deprecation warning remains. PostgreSQL tests ran outside the Windows restricted
token sandbox, against throwaway local databases; their servers were shut down.
The database suite remains optional/local rather than part of default CI; no
remote CI or Python 3.11 run was performed.

**Intermittent existing-test finding:** the first combined run discovered 91
tests and ended with 90 passes and one error in
`Batch1DbTests.test_oldest_due_work_and_concurrent_claims_are_distinct`:
`KeyError: 'ticket'` when the test expected every concurrent claim response to
contain a ticket. This occurred in the pre-existing Batch 1 test database,
before the optional vision schema was installed in a separate database.
Three isolated reruns all passed, as did the final expanded 92-test run. The
failed run's exact non-ticket refusal reason was not captured, so its cause is
not proven; contention-sensitive test/API behavior remains a risk to investigate
separately. No Layer 7 queue function or existing test was changed to suppress it.
The final passing run does not erase this observation.

New tests cover strict storage paths/ownership, format and stream limits,
redirect/auth/encoding rejection, malformed PNGs and decompression overflows,
metadata stripping, frozen-copy parity, bounded structured output, multiple
issues within one photo, hazard-preserving abstention, disagreement/text urgency,
disabled/shadow application startup, off-event-loop work and failure isolation.
PostgreSQL adds concurrent discovery/claims, late and removed attachments,
report/text-analysis/object changes, withdrawal, stale-generation/expired-token
fences, retry exhaustion/backoff, deadline fencing of a late thread, transaction
rollback and lost-response replay, cursor fairness, oversized discovery rejection,
client-role denial/RLS, durable minimized mock provenance, and unchanged tickets,
history, staff decisions and emergency notifications.

All images, provider responses, token counts and findings used by the new tests
are clearly marked unit fixtures. None is evidence of real Claude accuracy,
safety recall, privacy effectiveness or an approved production model.
