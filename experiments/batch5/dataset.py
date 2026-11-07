"""Dataset versioning, immutable bundle creation, and frozen-benchmark protection.

Manages versioned fine-tuning datasets created from adjudicated staff corrections
and synthetic fixtures, enforcing cryptographic commitments and group isolation.
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any

from .adjudication import filter_approved_adjudications
from .common import (
    DatasetError, GateBlockedError, ROOT, canonical, digest, file_hash, lines,
    normalize_text, private_target, read_json, read_jsonl, require, token,
    verify_frozen, write_json, write_new
)

SPLITS = ("train", "val", "test")


def split_adjudicated_records(
    records: list[dict[str, Any]],
    seed: str,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
) -> dict[str, list[dict[str, Any]]]:
    """Deterministically partition records into train, val, and test splits."""
    if not token(seed):
        raise DatasetError("A valid seed token is required")

    partitions: dict[str, list[dict[str, Any]]] = {s: [] for s in SPLITS}
    for record in records:
        group_key = str(record.get("ticket_id") or record.get("correction_id"))
        val = int(digest([seed, group_key]), 16) % 1000
        if val < int(train_ratio * 1000):
            partitions["train"].append(record)
        elif val < int((train_ratio + val_ratio) * 1000):
            partitions["val"].append(record)
        else:
            partitions["test"].append(record)
    return partitions


def create_dataset_bundle(
    out_dir: Path,
    records: list[dict[str, Any]],
    policy: dict[str, Any],
    version: str,
    seed: str,
    *,
    custodian_ref: str,
    approval_ref: str,
    allow_test_fixtures: bool = True,
) -> dict[str, Any]:
    """Package adjudicated records into an immutable, versioned training bundle."""
    if not token(custodian_ref) or not token(approval_ref):
        raise GateBlockedError("Approved custodian_ref and approval_ref tokens are required")
    if not (isinstance(version, str) and version.strip()):
        raise DatasetError("Dataset version string is required")

    # Gate: strict check that all records are ADJUDICATED_APPROVED
    approved_records = filter_approved_adjudications(records, enforce_gate=True)
    if not approved_records:
        raise DatasetError("No approved, adjudicated records supplied")
    if any(r['policy_sha256']!=digest(policy) or r['policy_version']!=policy.get('version') for r in approved_records):
        raise DatasetError('Adjudications must bind the exact supplied policy')
    origins=sorted({r['correction_snapshot']['origin'] for r in approved_records})
    if origins!=['synthetic_fixture']:
        raise GateBlockedError('Real correction datasets require a separately approved governance implementation')
    if not allow_test_fixtures:
        raise GateBlockedError('Synthetic fixture datasets were explicitly disabled')

    # Protection: check against frozen benchmark
    verify_frozen()
    with (ROOT / "evaluation/campusfix_benchmark_v2.csv").open(encoding="utf-8-sig", newline="") as stream:
        frozen_texts = {normalize_text(row["text"]) for row in csv.DictReader(stream)}

    for rec in approved_records:
        text = rec["correction_snapshot"]["report_text"]
        norm = normalize_text(text)
        if norm in frozen_texts:
            raise GateBlockedError("Dataset candidate duplicates a frozen benchmark case")
        norm_words = set(norm.split())
        if any(min(len(norm_words), len(set(f.split()))) >= 10
               and len(norm_words & set(f.split())) / len(norm_words | set(f.split())) >= 0.9
               for f in frozen_texts):
            raise GateBlockedError("Dataset candidate is a near-duplicate of a frozen benchmark case")

    # Partition records
    partitions = split_adjudicated_records(approved_records, seed)

    out_dir = private_target(out_dir)
    if out_dir.exists():
        raise DatasetError(f"Target dataset directory already exists: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    file_hashes: dict[str, str] = {}
    for split_name in SPLITS:
        rows = partitions[split_name]
        split_path = out_dir / f"{split_name}.jsonl"
        # Extract minimal structured training projection
        formatted_rows = [
            {
                "case_id": r["adjudication_id"],
                "ticket_id": r["ticket_id"],
                "text": r["correction_snapshot"]["report_text"],
                "label": r["final_label"],
                "verdict": r["verdict"],
                "adjudicator_id": r["adjudicator_id"],
                "policy_version": r["policy_version"],
            }
            for r in rows
        ]
        write_new(split_path, lines(formatted_rows))
        file_hashes[f"{split_name}.jsonl"] = file_hash(split_path)

    policy_path = out_dir / "policy.json"
    write_json(policy_path, policy)
    file_hashes["policy.json"] = file_hash(policy_path)

    manifest: dict[str, Any] = {
        "protocol_version": "batch5-v1",
        "dataset_version": version,
        "status": "RESEARCH_ONLY",
        "origins": origins,
        "policy_version": policy.get("version", "v3-proposed"),
        "policy_sha256": digest(policy),
        "split_seed": seed,
        "custodian_ref": custodian_ref,
        "approval_ref": approval_ref,
        "file_hashes": file_hashes,
        "counts": {s: len(r) for s, r in partitions.items()},
        "total_count": len(approved_records),
        "content_commitment": digest(sorted(r["correction_snapshot"]["report_text"] for r in approved_records)),
    }
    manifest["dataset_id"] = digest(manifest)
    write_json(out_dir / "manifest.json", manifest)
    return manifest


def verify_dataset_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Verify cryptographic commitments and partition file hashes of a dataset bundle."""
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        raise DatasetError(f"Manifest not found in {bundle_dir}")
    manifest = read_json(manifest_path)

    expected_id = manifest.get("dataset_id")
    unhashed = {k: v for k, v in manifest.items() if k != "dataset_id"}
    if digest(unhashed) != expected_id:
        raise DatasetError("Dataset manifest ID mismatch: manifest has been altered")

    if set(manifest.get('file_hashes',{}))!={'train.jsonl','val.jsonl','test.jsonl','policy.json'}:
        raise DatasetError('Dataset manifest file set is invalid')

    # Check file hashes
    for filename, expected_hash in manifest.get("file_hashes", {}).items():
        target_file = (bundle_dir / filename).resolve()
        if bundle_dir.resolve() not in target_file.parents:
            raise DatasetError('Dataset files must remain within the bundle')
        if not target_file.exists():
            raise DatasetError(f"Missing partition file in dataset bundle: {filename}")
        if file_hash(target_file) != expected_hash:
            raise DatasetError(f"File integrity mismatch for {filename}")

    if digest(read_json(bundle_dir/'policy.json'))!=manifest.get('policy_sha256'):
        raise DatasetError('Dataset policy commitment mismatch')

    return manifest
