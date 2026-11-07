# Batch 5: staff-correction provenance, adjudication, active-learning selection, dataset versioning, and reproducible LoRA/QLoRA/distillation experiment infrastructure

Date: 2026-11-06. Branch: `layer7-development`.

**Scope delivered:** human staff-correction provenance tracking, independent policy
adjudication protocol, multi-criteria active-learning selection, cryptographic dataset
versioning, instruction-tuning data formatters, and declarative LoRA / QLoRA /
distillation experiment infrastructure with offline deterministic simulation.
The implementation uses synthetic fixtures exclusively (`origin: synthetic_fixture`).
Raw operational staff corrections default to **PENDING_ADJUDICATION**; training or
dataset bundling directly on unapproved corrections is strictly prohibited by
enforced programmatic gates. No paid APIs, external model downloads, remote deployments,
git commits, or pushes were performed.

---

## 1. Architecture and Security Principles

### Staff-correction provenance (`experiments/batch5/correction.py`)
In an operational campus ticketing system, human staff routinely reclassify, transfer,
or adjust priority on tickets previously triaged by AI models. However, operational
transfers often reflect temporary workload balancing, organizational boundaries, or
ad-hoc workarounds rather than canonical campus policy.

The provenance engine captures the complete operational divergence vector:
1. `ticket_id` and monotonic `ticket_revision` counter.
2. Verified `report_text` screened for PII and frozen-benchmark collision.
3. `ai_verdict`: prior model identifier, claim/analysis token, suggested department,
   issue type, priority, confidence score, and routing timestamp.
4. `staff_action`: pseudonymized `staff_user_id`, authorized role (`DEPARTMENT_STAFF`,
   `DEPARTMENT_ADMIN`, `SYSTEM_ADMIN`), target department, issue, priority, action type
   (`TRANSFER`, `PRIORITY_OVERRIDE`, `RECLASSIFY`, `RESOLVE`), explicit correction reason,
   staff notes, and correction timestamp.
5. `disagreement`: explicit booleans for department, priority, and issue type changes,
   along with urgency divergence (`UPGRADED_TO_URGENT`, `DOWNGRADED_URGENT`, `AGREED_URGENT`,
   `RETAINED_NON_URGENT`).
6. Cryptographic commitment: `provenance_sha256 = digest(record)`.
7. **Mandatory gate:** `status: PENDING_ADJUDICATION`. Raw corrections are untrusted
   labels and cannot enter any model training flow.

### Independent policy adjudication (`experiments/batch5/adjudication.py`)
To prevent administrative shortcuts or human error from degrading model training, every
candidate correction must undergo independent policy adjudication:
- **Independence:** The adjudicator must be distinct from the staff corrector
  (`adjudicator_id != staff_user_id`); self-approval raises `GateBlockedError`.
- **Verdicts:**
  - `AFFIRMED_CORRECTION`: Staff override is verified correct according to campus policy.
  - `AFFIRMED_AI`: Prior AI verdict was correct per policy; staff transferred for secondary
    operational reasons. The AI verdict is recorded as ground truth.
  - `REJECTED_BOTH`: Both AI and staff made incorrect classifications; the adjudicator
    assigns the canonical policy label.
  - `POLICY_AMBIGUITY`: Unresolvable under current policy guidelines. Excluded from
    training (`AMBIGUOUS_EXCLUDED`).
- **Cryptographic linkage:** Adjudication records commit to the exact correction SHA-256,
  the target policy version/hash, an administrative `approval_ref`, and justification.
- **Enforcement gate:** `filter_approved_adjudications()` raises `GateBlockedError` if any
  unadjudicated or ambiguous record is fed into downstream dataset or experiment builders.

### Active-learning selection engine (`experiments/batch5/active_learning.py`)
Rather than random sampling, active learning identifies the most informative and
safety-critical cases for human review and fine-tuning:
1. **Uncertainty sampling:** Least-confidence ($1 - \max p$), margin ($1 - (p_1 - p_2)$),
   and normalized Shannon entropy ($-\sum p \log_2 p / \log_2 K$).
2. **Disagreement divergence:** Weighted divergence between AI and staff overrides
   (department 0.40, priority 0.35, issue type 0.25).
3. **Safety risk priority:** Keyword hazard screening (`smoke`, `fire`, `gas`, `sewage`,
   `electrical`, `shock`, `flood`, `collapse`, etc.) combined with priority mismatch. Cases
   where staff upgraded to `URGENT` or where hazards were triaged as Low/Medium receive
   top priority boost (+0.60).
4. **Diversity and near-duplicate suppression:** Word Jaccard similarity ($J \ge 0.85$)
   suppresses duplicate reports of identical incidents, while department quotas prevent
   high-frequency categories (e.g. routine plumbing) from starving rare categories.

### Dataset versioning and benchmark isolation (`experiments/batch5/dataset.py`)
1. **Immutable bundle creation:** Generates partitioned dataset directories containing
   `train.jsonl` (70%), `val.jsonl` (15%), `test.jsonl` (15%), `policy.json`, and
   `manifest.json`.
2. **Benchmark contamination barrier:** Every candidate text is normalized and compared
   against the frozen benchmark (`evaluation/campusfix_benchmark_v2.csv`). Exact matches
   and near-duplicates ($J \ge 0.90$) are blocked immediately (`GateBlockedError`).
3. **Cryptographic manifest:** `manifest.json` pins `dataset_id`, semantic `version`,
   policy hash, split seed, file hashes for all partitions, and `content_commitment`.
   `verify_dataset_bundle()` verifies file digests on load.

### Parameter-efficient adaptation: LoRA and QLoRA (`experiments/batch5/lora.py`)
Provides declarative experiment planning and simulation for fine-tuning open-weights models
(e.g., Qwen 2.5 7B, DeepSeek-R1 Distill Qwen 8B, Qwen 2.5 1.5B):
- **Configurations:** Rank $r \in \{4, 8, 16, 32, 64\}$, $\alpha$, dropout, target modules
  (`q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, `down_proj`),
  quantization modes (`4bit` QLoRA NF4, `8bit`, `none` 16-bit LoRA).
- **Exact parameter accounting:** Calculates base parameters and low-rank adapter
  matrices ($2 \times r \times d$), demonstrating that LoRA updates $< 1\%$ of model weights.
- **Artifact contracts:** Emits standard HuggingFace PEFT `adapter_config.json`, training
  manifests, and experiment audit receipts.
- **Offline simulation:** Deterministically simulates training steps, cosine learning rate
  decay, gradient accumulation, and loss trajectory without requiring GPUs or weight downloads.

### Knowledge distillation infrastructure (`experiments/batch5/distillation.py`)
Provides declarative student distillation from larger teacher models (DeepSeek-R1 8B,
Qwen 14B) into compact student models (Qwen 1.5B, 8B):
- **Temperature scaling:** Softens teacher and student logit distributions via temperature $\tau \ge 1.0$:
  $$P_i^\tau = \frac{\exp(z_i / \tau)}{\sum_j \exp(z_j / \tau)}$$
- **Combined loss:** Balances Kullback-Leibler divergence with task cross-entropy:
  $$\mathcal{L} = \alpha_{\text{KD}} \cdot \tau^2 \cdot D_{\text{KL}}(P_{\text{student}}^\tau \parallel P_{\text{teacher}}^\tau) + (1 - \alpha_{\text{KD}}) \cdot \mathcal{L}_{\text{CE}}(y_{\text{true}}, P_{\text{student}})$$
- **Offline simulation:** Validates temperature scaling preserves gradient scale ($\tau^2$)
  and emits reproducible audit receipts.

---

## 2. Files Changed and Added for Batch 5

| File | Purpose |
|---|---|
| `experiments/batch5/__init__.py` | Package boundary for Batch 5 active learning and experiment tools |
| `experiments/batch5/common.py` | Common error hierarchy (`GateBlockedError`, `ProvenanceError`), PII screening, text normalization, and commitments |
| `experiments/batch5/correction.py` | Staff-correction provenance tracking, disagreement analysis, and `PENDING_ADJUDICATION` gate |
| `experiments/batch5/adjudication.py` | Independent policy adjudication framework, consensus verdicts, and approval gating |
| `experiments/batch5/active_learning.py` | Multi-criteria active learning selection engine (uncertainty, divergence, safety priority, diversity) |
| `experiments/batch5/dataset.py` | Versioned dataset bundle generation, partition hashing, group isolation, and benchmark leak prevention |
| `experiments/batch5/formatters.py` | ChatML and prompt/completion instruction formatting |
| `experiments/batch5/lora.py` | Declarative LoRA/QLoRA config, parameter accounting, PEFT config generation, and offline training simulation |
| `experiments/batch5/distillation.py` | Teacher-student distillation config, temperature scaling, KL loss, and offline simulation |
| `experiments/batch5/fixtures.py` | Synthetic correction fixtures covering diverse departments, transfers, and safety escalations |
| `experiments/batch5/__main__.py` | Unified CLI runner (`provenance`, `adjudicate`, `active-learning`, `dataset`, `lora-plan`, `distill-plan`, `simulate`) |
| `experiments/batch5/tests/__init__.py` | Test package declaration |
| `experiments/batch5/tests/test_batch5.py` | 21 comprehensive unit and integration tests covering all Batch 5 components |
| `.github/workflows/ci.yml` | Added Batch 5 test protocol step to continuous integration |
| `docs/BATCH5_ACTIVE_LEARNING_AND_DISTILLATION.md` | This technical operating and governance report |

---

## 3. Local Verification Commands

All test suites execute in the repository's isolated Python 3.12.16 virtual environment
(`.venv-dbtest312`):

```powershell
# 1. Batch 5 unit test suite (21 tests)
.venv-dbtest312/Scripts/python.exe -m unittest discover -s experiments/batch5/tests -v

# 2. Batch 5 CLI subcommands (provenance, adjudication, active learning, dataset, LoRA, distillation, simulation)
.venv-dbtest312/Scripts/python.exe -m experiments.batch5 provenance --out .campusfix-private/batch5/provenance.json
.venv-dbtest312/Scripts/python.exe -m experiments.batch5 adjudicate --out .campusfix-private/batch5/adjudication.json
.venv-dbtest312/Scripts/python.exe -m experiments.batch5 active-learning --out .campusfix-private/batch5/active_learning.json --batch-size 3
.venv-dbtest312/Scripts/python.exe -m experiments.batch5 dataset --out .campusfix-private/batch5/dataset_bundle --version batch5-v1.0.0
.venv-dbtest312/Scripts/python.exe -m experiments.batch5 lora-plan --out .campusfix-private/batch5/lora_plan.json
.venv-dbtest312/Scripts/python.exe -m experiments.batch5 distill-plan --out .campusfix-private/batch5/distill_plan.json
.venv-dbtest312/Scripts/python.exe -m experiments.batch5 simulate --dataset .campusfix-private/batch5/dataset_bundle --out .campusfix-private/batch5/simulation

# 3. Backend unit regression tests (111 tests)
Push-Location backend
../.venv-dbtest312/Scripts/python.exe -m unittest discover -s tests -q
Pop-Location

# 4. Local PostgreSQL database tests (111 tests)
.venv-dbtest312/Scripts/python.exe -m unittest discover -s supabase/tests/local -q

# 5. Batch 2 offline data protocol (49 tests)
.venv-dbtest312/Scripts/python.exe -m unittest discover -s experiments/batch2/tests -q

# 6. Frozen benchmark evaluation and metric reproduction
.venv-dbtest312/Scripts/python.exe evaluation/reproduce_metrics.py --check
.venv-dbtest312/Scripts/python.exe -m unittest discover -s evaluation -q

# 7. Compilation check
Push-Location backend
../.venv-dbtest312/Scripts/python.exe -m compileall -q app
Pop-Location
.venv-dbtest312/Scripts/python.exe -m compileall -q experiments/batch5
git diff --check
```

---

## 4. Final Local Results

Executed with Python **3.12.16** on Windows:

| Suite / Check | Discovered | Passed | Skipped / Failed | Status |
|---|---|---|---|---|
| **Batch 5 Test Suite** (`experiments/batch5/tests/`) | 21 | **21 passed** | 0 | **OK** |
| **Backend Unit Suite** (`backend/tests/`) | 111 | **111 passed** | 0 | **OK** |
| **Local PostgreSQL Suite** (`supabase/tests/local/`) | 111 | **111 passed** | 0 | **OK** |
| **Batch 2 Offline Protocol** (`experiments/batch2/tests/`) | 49 | **49 passed** | 0 | **OK** |
| **Frozen Offline Evaluation** (`evaluation/`) | 118 | **115 passed** | 3 skipped | **OK** (Optional dependencies) |
| **Frozen Metric Reproduction** (`reproduce_metrics.py --check`) | N/A | **Passed** | 0 | **OK** (DeepSeek 51/51/40, Qwen 50/53/34) |
| **Module Compilation** (`compileall -q`) & `git diff --check` | N/A | **Clean** | 0 | **OK** (Zero syntax or whitespace issues) |

### Active Learning and Simulation Highlights

1. **Active Learning Ranking:**
   - Top selected sample: Case 5003 (dorm microwave sparking/smoke), successfully prioritized
     due to the safety risk score boost (+0.60 for staff upgrade to `URGENT` on electrical hazard).
   - Near-duplicate suppression successfully filtered redundant issue reports.
2. **LoRA Parameter Efficiency:**
   - Base model: `Qwen/Qwen2.5-7B-Instruct` (approx. 7.6B parameters).
   - Adapter rank $r = 16$, targeting all 7 projection modules.
   - Trainable parameters: 46,284,800 ($\approx 0.604\%$ of base parameters).
3. **Simulation Convergence:**
   - LoRA simulated training loss: decreased monotonically from 2.450 to 2.4212 over 2 epochs.
   - Distillation simulated loss: decreased from 2.100 to 2.0766 with $\tau = 2.0$ temperature scaling.

---

## 5. Decisions and Data-Approval Gates

Work is stopped at the policy and data-approval boundaries:

1. **Approval of Real Staff Corrections Ingestion:**
   - Real staff operational overrides cannot be extracted or processed until institutional
     data governance officers authorize the specific extraction scope, retention policies,
     and de-identification protocols.
2. **Adjudication Panel Authorization:**
   - Campus policy authorities must formally appoint certified independent adjudicators
     and publish binding resolution guidelines for departmental border disputes (e.g., smart
     podiums, exterior security lighting, card-swipe vending).
3. **Active Learning Batch Size and Review Cadence:**
   - Operational limits on weekly active-learning queues for human adjudicators must be
     established before triggering review workflows.
4. **Model Architecture and Live Training Clearance:**
   - Selection of target base model for fine-tuning (`Qwen2.5-7B` vs `DeepSeek-R1-Distill-8B`
     vs `Qwen2.5-1.5B`) requires budget, hardware, and inference latency approvals.
   - Actual model parameter training remains gated until approved datasets with signed
     custodial manifests are delivered.

No paid APIs, real report extractions, production migrations, deployments, commits, or pushes occurred.

---

## 6. Rollback

To rollback Batch 5 changes locally:
1. Delete `experiments/batch5/` and `.campusfix-private/batch5/`.
2. Remove the Batch 5 step from `.github/workflows/ci.yml`.
3. Delete this report (`docs/BATCH5_ACTIVE_LEARNING_AND_DISTILLATION.md`).
4. Batches 1–4, backend routing, database migrations, and frozen benchmarks remain completely
   untouched and functional.
