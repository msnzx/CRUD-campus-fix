# Batch 2 offline research tools

Read [the audit and operating procedure](../../docs/BATCH2_AI_DATA_AND_EVALUATION.md)
and [the proposed policy](../../docs/CLASSIFICATION_POLICY_V3_PROPOSED.md) before
supplying reports or predictions. This package imports reviewed artifacts; it
does not call models, retrieve production data, or promote a model.

From the repository root, using the existing Python 3.11+ environment:

```powershell
python -m experiments.batch2 --help
python -m experiments.batch2 plan --out .campusfix-private/plan.json
python -m unittest discover -s experiments/batch2/tests -v
```

Each subcommand has `--help`. New artifacts must use unused private paths. The
plan contains NOT_RUN experiments and actual prompt/source snapshots, with
missing weight digests explicitly unset. No real data or model accuracy evidence
is bundled. Test-generated reports, labels, approvals and predictions are marked
unit fixtures, live only in temporary directories, and cannot pass real-data CLI
test gates.

Modules:

- `common.py`: private exclusive-create I/O and integrity checks.
- `policy.py`, `policy_v3_proposed.json`: label schema and pending approval gates.
- `dataset.py`: reviewed ingestion, source revisions, two blinded annotations,
  independent adjudication, pre-annotation group reservations and dataset bundles.
- `benchmark.py`: imported prediction validation, calibration, abstention,
  safety/error metrics and paired candidate comparison.
- `protocol.py`: reproducible plans, validation selection and custodian final test.
- `__main__.py`: offline CLI; no `.env` loading or inference integration.

Reuse of frozen taxonomy/metric helpers is read-only. Existing `evaluation/`
files and the production backend remain separate and unchanged by this package.
