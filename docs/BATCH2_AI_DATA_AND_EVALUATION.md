# Batch 2: classification, real-data governance and offline evaluation

Date: 2026-11-06. Branch: `layer7-development`.
Scope: offline research tooling, policy proposal and audit. No model inference,
real-data collection, SMTP, hosted database changes or deployment was performed.

## Audit findings

| Component and supporting files | Finding | Status / remaining work |
|---|---|---|
| `backend/app/classify.py`, `main.py` | DeepSeek-R1 8B is the current runtime classifier. Its 0/1 route flags are routing decisions, not calibrated probabilities. Batch 1 preserves URGENT independently of routing. | Existing behavior preserved; genuine confidence measurement remains blocked on an approved score source |
| `evaluation/README.md`, `metrics.json`, `artifacts.sha256.json` | Frozen synthetic v2: 249 cases, 187 development and 62 already-used historical holdout cases. Four original artifacts are SHA-pinned. | Complete historical archive; not an independent future test set |
| `evaluation/reproduce_metrics.py` | Offline replay reproduces DeepSeek issue/department/priority correct counts 51/51/40 of 62; Qwen **8B** 50/53/34. Historical mean times 19.3657s and 2.7073s. | Reproduced, not newly inferred; does not establish current runtime accuracy or comparable hardware latency |
| `evaluation/dev_audit/DEV_AUDIT_REPORT.md`, `analyze_development.py`, `dev_inputs.sha256.json` | Development comparison is DeepSeek 8B, Qwen **14B**, and Laya. Different adapters/output spaces confound direct comparison; original development prompts/scripts were not preserved. DeepSeek under-triaged 8/36 urgent cases; Qwen 14B found all 36 but predicted 72 urgent cases. | Historical observations only, labels unreviewed; no Qwen 14B holdout evidence or Qwen 8B development evidence |
| `evaluation/dev_audit/CLASSIFICATION_POLICY_v3.md`, `v3_proposal/` | Draft policy, 17 unresolved questions, 38 pending proposed label changes and three pending exceptions. The old review/safety coupling conflicts with approved Batch 1 behavior. | Retained byte-for-byte; no pending label promoted |
| `experiments/batch2/policy.py`, `policy_v3_proposed.json` | Proposed versioned independent priority/action contract and 23-decision approval register. | Implemented validation; policy remains proposed |
| `experiments/batch2/dataset.py` | Reviewed-input ingestion, keyed provenance, leak screening, blinded adjudication and group-isolated splits. | Implemented offline; actual authorized reports and reviewers not supplied |
| `experiments/batch2/benchmark.py` | Imported-prediction scoring, isotonic calibration, selective routing, urgent recall, uncertainty and paired error analysis. Reuses existing metric/taxonomy helpers read-only. | Implemented and fixture-tested; no real calibrator fitted or release thresholds chosen |
| `experiments/batch2/protocol.py` | Reproducible experiment plan, validation preregistration and custodian-controlled independent test procedure. | Implemented mechanical guards; external custody and model experiments still required |

The frozen holdout was only read programmatically for existing metric replay,
integrity tests and conservative duplicate exclusion. No holdout text was used
for prompt edits, policy resolution, threshold fitting or model selection.

## Responsibilities and boundaries

The AI developer owns policy/evaluation contracts, safe research data preparation,
annotation provenance, model/adapter measurements, calibration and experiment
analysis. Campus policy owners must resolve triage and release-risk decisions.
The data controller authorizes access, purpose, retention and deletion; human
reviewers and the independent custodian perform the attested work. An identifier
in a JSON file does not authenticate them.

No frontend work or new application endpoints are needed. General backend Auth,
RLS, production SMTP, deployment and platform configuration stay separate. Prior
audit Auth/RLS concerns are not fixed or revalidated by research metrics. There
are no new frameworks, dependencies, database migrations or runtime model hooks.
Layers 5–6, the evaluation archive, and existing Batch 1 / Layer 7 code were not
edited for Batch 2.

## Real-data workflow

Run `python -m experiments.batch2 --help` from the repository root. Python 3.11+
and the standard library suffice; the tested interpreter was Python 3.12.16 with
the existing locked backend environment. Every command requires `--out`; outputs
are exclusive-create and cannot overwrite an artifact. Repository outputs must
be under gitignored `.campusfix-private/`. A custodian may use a protected path
outside the repository. The CLI never loads `.env`, imports the running backend,
contacts Supabase, or calls a model.

1. Obtain B2-03/B2-04 approval before collecting reports. Define population,
   collection window, lawful authority, exclusions, incident grouping, split seed,
   retention, deletion and access controls. Keep raw reports and any ID mapping in
   controller-managed storage outside the repository. Do not export media, contact
   details, free-form metadata, credentials or raw database rows into this tool.
2. A human privacy reviewer supplies only de-identified report text and the exact
   reviewed-input schema below. A grouping reviewer associates all reports from
   the same incident, follow-up thread, repeated event or near-duplicate cluster.
   Use the same private HMAC key across the dataset lineage. Supply it through
   `CAMPUSFIX_DATASET_HMAC_KEY` (at least 32 bytes); never commit it or pass it as a
   command-line argument. Key rotation changes IDs and requires lineage review.
3. `ingest --records reviewed.jsonl --out <private>/cases.jsonl` screens common
   identifiers/secrets, timestamps, exact duplicates, conservative near-duplicates
   and frozen-benchmark overlap. It exports keyed case/incident/text fingerprints,
   reviewed text and approval provenance, never raw source/incident IDs or the key.
   A rejected record returns to review; no silent dropping or automatic rewriting.
4. **Before annotation or model exposure**, run `reserve --cases ... --seed ...
   --custodian ... --approval-ref ... --out ...`. This commits the sources and
   deterministic group assignments. The proposed allocation is 50% development,
   20% calibration, 15% validation and 15% independent test by hash of incident
   group and seed. It is not label-stratified and exact counts vary. Approve this
   allocation in B2-04; do not search seeds to improve label balance. If partitions
   lack sufficient groups/support, collect more independent reports under a
   prospectively amended plan rather than inspecting test outcomes.
5. Resolve/approve the policy before final labeling. Two distinct human annotators
   label independently, blinded to model predictions and each other's initial
   vote. `annotation-template --cases ... --annotator ... [--policy ...] --out ...`
   supplies empty labels, never suggested answers. Templates wrap display text and
   an `annotation`; save the completed **inner annotation records** as JSONL for
   ingestion. Then `adjudication-template --cases ... --annotations ...
   --adjudicator ... [--policy ...] --out ...` supplies empty adjudication records.
   A third person reviews agreements/disagreements; save completed **inner
   adjudication records**. No model can adjudicate its own target labels.
6. `build --cases ... --annotations ... --adjudications ... --policy ... --seed ...
   --custodian ... --reservation ... --out <new-private-bundle>` validates complete
   coverage, independent identities, chronological reviews, exact vote and policy
   hashes, source-revision bindings, explicit disagreement reasons and the
   unchanged split reservation. Both annotation and adjudication contain a
   `case_revision_sha256` over the reviewed source fields; changed text or
   provenance cannot silently reuse old labels.
   The bundle includes full provenance, file hashes, group counts and origin flags.
   Changing policy or source content requires re-review and a new version.

Reviewed source JSONL has exactly these fields:

```text
source_id, incident_id, source_kind="real_world", text, collected_at,
authority_ref, privacy_review, group_review
```

Both review objects contain exactly `status="APPROVED"`, `reviewer_id`,
`reviewed_at`, and `approval_ref`. References are pseudonymous tokens, not names,
emails or report content. Timestamps must be timezone-aware, not future, and in
collection → privacy/group review → annotation → adjudication order. Labels use
the six-field contract in `CLASSIFICATION_POLICY_V3_PROPOSED.md`.

Regex screening is deliberately conservative and **does not guarantee
anonymization**: names, rare events, indirect identifiers and unusual PII require
human review. Incident hashing cannot prove real-world independence or catch all
semantic duplicates. Similarity checks are quadratic and intended for a modest
reviewed corpus; large-scale ingestion needs a measured indexing design later.
Private artifacts still contain sensitive report context and pseudonymous links.
Gitignore is not an access control, and mode 0600 does not establish Windows ACLs.
The controller must configure storage permissions, encryption/backup policy and
retention/deletion. This tooling does not automatically delete raw sources or
audit copies. A required deletion invalidates affected dataset lineage and test
commitments; document the withdrawal and build a reviewed replacement. Never
silently edit a sealed release.

## Independent test custody

The **custodian**, not the tuning developer, builds and retains the full bundle.
Provide developers only `manifest.json`, `policy.json`, and `work/`. Development
verification deliberately skips sealed file reads and works with those files
absent. Custodian-only annotators must not share independent test text, labels,
case-level adjudications or error analysis with prompt/model developers. Reserve
and label before running candidate inference. Grouping/sampling approval must
cover repeated incidents and any already-exposed data.

Use development for bounded iteration; calibration only to fit confidence;
validation to choose the candidate and threshold under a predeclared budget.
`register` requires an approved policy with all decisions resolved, real-world
origins, a valid calibrator, explicit criteria, a threshold grid, approval and
custodian references. It binds candidate/model/prompt/code/policy/dataset hashes,
calibrator, validation predictions, threshold, test commitments and tool source.
Registration alone does not open test data.

The custodian runs `test-inputs --bundle ... --registration ... --out ...` once
selection is frozen. Only case IDs and report text are exported, without labels
or incident IDs. A release receipt prevents selecting a different registration
after exposure. Produce predictions with that exact candidate and return them to
the custodian. `final-score --bundle ... --predictions ... --run ... --calibrator
... --registration ... --out ...` consumes an exclusive attempt receipt **before
scoring labels**. Concurrent scorers yield one winner. A crash after that receipt
spends the attempt; changing output names does not enable a second look. Recover
only under a documented independent review/new test protocol, never by deleting
the receipt to tune against the same data. Freeze tools before registration;
source changes invalidate the registration.

Final output omits per-case errors and reports aggregate metrics and whether
the preregistered criteria passed; it always sets `production_promotion=false`.
A failed test does not authorize more tuning on that test; retain the outcome
and collect a new independent cohort. Mechanical guards apply per bundle, not
against someone copying it, editing receipts/code or reading files manually.
Checksums detect accidental mutation, not malicious forgery. Real independence
requires external custody/ACLs and a registry of dataset exposures/attempts; this
package is not an IAM system, tamper-proof registry or evidence of independence
by itself. Unit-test fixture bypasses are library-only and cannot produce real
evaluation evidence through the CLI.

## Prediction and run contracts

All metrics consume recorded predictions; there is no inference runner. Produce
JSONL containing exactly one row per partition case with:

```text
case_id, status, issue_type, department, priority, action, safety_signal,
confidence_score, latency_seconds, error_kind
```

`status` is ok/error/invalid. Successful rows use the approved taxonomy and
independent action; an URGENT priority requires a true safety signal, including
Other/review. Failures explicitly use null issue/department/priority/score,
NEEDS_REVIEW, false safety and a pseudonymous error-kind token. They remain in
denominators, count as urgent misses when applicable, and cannot silently vanish.
Do not copy raw exceptions or reports into error metadata. Successful confidence
scores are optional for uncalibrated analysis; fitting requires measured scores.

Each run JSON contains exactly:

```text
candidate_id, model_name, model_digest, prompt_sha256, prompt_version,
adapter_version, code_sha256, decoding, score_kind, environment, partition_sha256
```

Digests are actual lowercase SHA-256 values, not guessed placeholders. Record
exact weights/quantization under `model_digest`; model tags alone are mutable.
`decoding` records temperature, think, stream and integer/null seed. `environment`
records python, ollama_version (NOT_RUN only for non-inference fixtures/plans) and
hardware. `partition_sha256` is the corresponding manifest file hash.
`score_kind` is model_probability, uncalibrated_score, route_flag or unavailable.
Self-reported certainty is not proof of calibrated probability. The producer and
reviewer must substantiate the score source; the importer cannot authenticate a
JSON claim or detect fabricated predictions. Candidate fingerprint binds every
run field except environment/partition; adapter changes prevent pooled paired
comparison and candidate changes invalidate calibration.

## Calibration, abstention and error analysis

- `fit` reads only calibration data and fits isotonic PAV to measured scores and
  joint issue/department/priority correctness on answerable labels. Review-required
  truth is not a safe auto-route. Ties are pooled, monotonicity enforced and end
  scores clipped by the learned step function. Missing/constant/route-flag scores
  and single-outcome samples are rejected; no artificial confidence is supplied.
- `score --split development|validation` reports axis accuracy/macro-F1, priority
  confusion, coverage, selective error, urgent priority/safety/auto-route recall,
  false alerts, review precision/recall, failures, latency and error categories.
  Null expected labels are explicit unknown classes, not ordinal priorities.
  Unknown priorities are excluded from false-alert negative support rather than
  treated as verified non-urgent cases; their support is reported separately.
  Failed outputs receive a failure sentinel, not credit for matching null labels.
- `score --calibrator ... --threshold ...` only abstains from an existing auto
  route; it cannot upgrade raw review decisions. Safety flags survive abstention.
  This is an offline routing-policy simulation and does not change runtime code.
- Raw/calibrated Brier score, ten equal-width-bin ECE and reliability summaries
  measure the declared joint correctness target. Fitting-set performance is not
  held-out calibration evidence. All-case failures and safety recall are reported
  separately from scored-success calibration observations.
- Wilson 95% intervals accompany risk, urgent recall and false-alert rate. Empty
  urgent/accepted support is null, never perfect. `curve` uses validation only;
  `register` selects maximum coverage among eligible thresholds (higher threshold
  breaks coverage ties). Criteria must explicitly supply `max_error`,
  `min_urgent_recall`, `min_accepted`, `min_urgent`, `max_false_alert_rate`, and
  `min_coverage`. Selection uses risk/FPR upper bounds and urgent-recall lower
  bounds plus support, not just point estimates. There are no production defaults.
  Wilson intervals assume independent observations; report clustering and choose
  support conservatively where incident groups contain correlated reports. A
  dedicated cluster-bootstrap analysis may be needed before a release decision.
- `compare` accepts a private JSON list of `{run: <path>, predictions: <path>}`,
  uses the same cohort/adapter, reports paired exact McNemar comparisons and
  flags differing recorded environments as unsuitable for direct latency claims.
  Multiplicity/candidate budget and repeated-validation overfit remain governance
  concerns; exploratory p-values do not authorize promotion.
- Error reports contain case IDs and categories, never report text. Development
  case review stays private; final independent-test output suppresses those IDs.

## Reproducible experiment preparation

`plan --out <private>/plan.json` writes a NOT_RUN plan with separate DeepSeek-R1
8B, Qwen3 8B and Qwen3 14B candidates, missing weight digests explicitly null,
runtime prompt snapshot and one proposed safety/review clarity prompt, prompt
hashes, canonical taxonomy order, source hashes, common decoding and three paired
repetitions. Runtime prompt extraction uses AST string evaluation, never backend
imports. The canonical taxonomy order is an offline snapshot; verify actual
runtime reference-data order before a future inference run.

Before executing later, approve the policy, candidate/prompt budget, score source,
sampling and acceptance criteria; fill actual weight/runtime digests; freeze the
common Batch 1 adapter; record seeded shuffled order, warm/cold state, hardware,
timeouts, parsing failures, per-case timing and all repetitions. Tune only on
development, fit on calibration, select once on validation, preregister and run
the independent test through its custodian. Repeated runs must not be cherry-picked
or treated as independent extra reports. An eventual approved inference producer
must create these exact prediction/run contracts. No Ollama/API execution or
download was needed to prepare or test this infrastructure.

## Local validation and rollback

Offline verification commands:

```powershell
.venv-dbtest312/Scripts/python.exe -m unittest discover -s experiments/batch2/tests -q
Push-Location backend
../.venv-dbtest312/Scripts/python.exe -m unittest discover -s tests -q
Pop-Location
.venv-dbtest312/Scripts/python.exe -m unittest discover -s evaluation -q
.venv-dbtest312/Scripts/python.exe evaluation/reproduce_metrics.py --check
.venv-dbtest312/Scripts/python.exe evaluation/dev_audit/analyze_development.py --check
```

Batch 2 adds `experiments/__init__.py`, `experiments/batch2/` (including tests),
this report and `CLASSIFICATION_POLICY_V3_PROPOSED.md`; modifies only `.gitignore`
and the offline CI step. Existing uncommitted backend, migrations and database
tests belong to earlier work and were retained. To undo only Batch 2, remove its
new package/docs and its CI step. Keep the private-data ignore rule until all
research artifacts are safely retained or disposed of under controller policy.
Do not reset the branch or delete earlier Batch 1/Layer 7 changes. No database
rollback, dependency downgrade or model change is required.

Executed results in the existing Python 3.12.16 environment:

| Check | Exact result |
|---|---|
| Batch 2 offline protocol tests | **49 passed**, zero skipped |
| Backend regression tests | **61 passed**, zero skipped |
| Throwaway PostgreSQL regression/concurrency tests | **73 passed**, zero skipped |
| Existing evaluation tests | **118 discovered: 115 passed, three skipped** |
| Frozen artifact integrity / recorded metrics `--check` | Passed, no inference |
| Development audit `--check` | Passed against available historical private prediction files |
| Runtime lock verification | All **42 pins match**; Python constraints and active dependency requirements checked across **49 installed distributions**, zero issues |
| Python compilation and `git diff --check` | Passed |

The PostgreSQL sandbox attempt ran zero tests and failed four class setup steps
because Windows could not create its restricted token (error 87). The same
existing harness then passed all 73 tests with broader local process permissions;
it used temporary localhost PostgreSQL and shut the server down. No hosted
database was contacted. The harness supplies Supabase platform shims and enables
RLS on policy-bearing tables, so this is not evidence of clean hosted RLS setup,
PostgREST/Auth integration or a live deployment.

The three evaluation skips are optional scikit-learn parity, Anthropic SDK and
jsonschema checks: those optional packages are absent. No package was installed
or downgraded. This environment has no pip executable/module, so dependency
verification used installed-distribution metadata, packaging markers/specifiers
and the lockfile rather than `pip check`. Existing FastAPI/Starlette tests emit a
deprecation warning; no dependency was changed to suppress it. Python 3.11, Linux
CI, frontend tests, real SMTP/Auth, inference, real-data calibration and final
independent model evaluation were not executed for Batch 2. CI remains configured
for Python 3.11; the new offline suite has been added but no remote CI run occurred.

The 49 tests include privacy rejection/no raw-ID export, origin and timestamp
validation, frozen overlap and duplicate quarantine, policy lineage/approval
guards, independent blinded labeling, vote/policy/source-revision binding,
explicit disagreement resolution, pre-annotation reservations, stable disjoint
group partitions, manifest corruption, development's inability to open sealed
files, PAV pooling/ties, score-source refusal, calibration scoping, preserved
URGENT under abstention, failure denominators, unknown-priority exclusions,
support/confidence-bound gates, paired comparison, final-data origin/approval
gates, label-free release, frozen registration, concurrent one-attempt claiming,
labels unopened before the claim, crash-after-claim behavior, output protection,
and an offline plan with distinct model sizes. All reports, approvals, labels and
predictions created by these tests are temporary **unit fixtures**, not real
reports, real human decisions or measured model results.

## Remaining decisions, experiments and implementation order

1. **Policy owners:** resolve U-01–U-17 and B2-01. Approve a versioned contract
   and operational triage boundaries before final labels or model selection.
   The proposed policy is ready for review; no unresolved safety policy was
   encoded as an operational decision.
2. **Controller/custodian:** resolve B2-03/B2-04; authorize and collect real
   reports, review privacy/grouping, reserve partitions, perform blinded
   annotation and independent adjudication. Acceptance requires documented
   authority, complete provenance and actual segregated test custody. A real
   dataset and humans are required; a model or paid API is not.
3. **AI developer:** resolve B2-05, instrument an approved genuine confidence
   source and record immutable weights/prompts/adapter/runtime manifests for
   DeepSeek and each Qwen size. Local model execution is needed later; an
   external/paid API is not inherently necessary. No confidence source has been
   measured by this batch, and a self-asserted JSON score does not satisfy this.
4. **AI developer + policy owner:** resolve B2-02/B2-06, including experiment
   budget and uncertainty-aware support/risk gates. Run paired baseline/candidate
   prompt experiments on development only with the common adapter and preserved
   failures; fit calibration only on its partition. Acceptance requires complete
   reproducible predictions and held-out calibration evidence, not fixture scores.
5. **AI developer:** select once on validation, review safety misses, over-alerts,
   abstention and subgroup/incident effects privately, then freeze and preregister.
   Underpowered or failing criteria yield no eligible threshold. Real model
   predictions and adjudicated data are required; deployment is not.
6. **Independent custodian:** release inputs, run the one-shot final evaluation
   and retain aggregate pass/fail evidence. If it fails or is compromised, plan
   a new independent cohort. Production promotion, runtime confidence integration
   and deployment require separate review and evidence; this package cannot
   perform them. General backend security blockers also remain applicable.

**Readiness:** Batch 2's offline infrastructure is implemented and locally
validated for review. Approved policy, authorized real data, measured model
confidence, independent custody and model-dependent experiments remain pending.
There is no new accuracy, recall improvement, calibrated production confidence,
model-selection winner, deployment or production-readiness claim.
