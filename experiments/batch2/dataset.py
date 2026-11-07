"""Reviewed-input ingestion, blinded human labeling and group-isolated partitions.

This is not an automatic anonymizer or a Supabase exporter. A privacy reviewer
must de-identify reports before ingestion; raw records are never copied to outputs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import hmac
from pathlib import Path
import re
import unicodedata

from .common import ROOT, canonical, digest, file_hash, lines, private_target, require, token, verify_frozen, write_new, write_json
from .policy import approved, policy_hash, validate_label

SPLITS = ("development", "calibration", "validation", "test")
PII_PATTERNS = (
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", r"https?://\S+", r"\b\d{3}-\d{2}-\d{4}\b",
    r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)",
    r"\b\d{7,}\b", r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    r"\b\d+\s+(?:\w+\s+){0,3}(?:street|avenue|road|lane|drive)\b",
    r"\b(?:room|apt|apartment|student\s*id|ticket\s*(?:id|#))\s*[:#-]?\s*[A-Z]?\d+\b",
    r"\b(?:sk-ant-|sb_secret_|eyJ)[A-Za-z0-9_\-.]{12,}",
)


def timestamp(value) -> str:
    require(isinstance(value, str), "Timestamp required")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        require(False, "Invalid timestamp")
    require(dt.tzinfo is not None and dt <= datetime.now(timezone.utc), "Timestamp must be timezone-aware and not future")
    return value


def screen_text(text) -> str:
    require(isinstance(text, str) and 1 <= len(text.strip()) <= 12000, "Reviewed text must contain 1–12000 characters")
    require(not any(re.search(p, text, re.I) for p in PII_PATTERNS), "Potential personal data or secret: return to privacy review")
    require(not any(unicodedata.category(c) in ("Cc", "Cf") and c not in "\n\r\t" for c in text),
            "Control characters require privacy review")
    return text


def normalized(text: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def pseudonym(key: bytes, domain: str, value: str) -> str:
    return hmac.new(key, (domain + "\0" + value).encode(), hashlib.sha256).hexdigest()


def ingest(records: list[dict], key: bytes, *, allow_test_fixtures=False) -> list[dict]:
    require(isinstance(key, bytes) and len(key) >= 32, "A private HMAC key of at least 32 bytes is required")
    require(bool(records), "No reviewed source records supplied")
    verify_frozen()
    # Only fingerprints are used to prevent recycling the old benchmark.
    with (ROOT / "evaluation/campusfix_benchmark_v2.csv").open(encoding="utf-8-sig", newline="") as stream:
        frozen_texts = {normalized(row["text"]) for row in csv.DictReader(stream)}
    result, seen_ids, seen_texts = [], set(), set()
    for row in records:
        allowed = {"source_id", "incident_id", "source_kind", "text", "collected_at", "authority_ref",
                   "privacy_review", "group_review"}
        require(set(row) == allowed, "Source fields must match the reviewed-input contract")
        require(row["source_kind"] == "real_world" or (allow_test_fixtures and row["source_kind"] == "test_fixture"),
                "Only real-world reports are accepted outside explicitly marked unit tests")
        require(token(row["source_id"]) and token(row["incident_id"]) and token(row["authority_ref"]), "Invalid provenance reference")
        require(not row["source_id"].startswith("CFB-"), "Frozen benchmark IDs cannot enter a new dataset")
        text = screen_text(row["text"])
        norm = normalized(text)
        require(norm and norm not in frozen_texts, "Report duplicates a frozen benchmark case")
        words=set(norm.split())
        require(not any(min(len(words),len(set(t.split())))>=10
                        and len(words & set(t.split()))/len(words | set(t.split()))>=0.9 for t in frozen_texts),
                "Report is a near-duplicate of a frozen benchmark case")
        collected = timestamp(row["collected_at"])
        for review in (row["privacy_review"], row["group_review"]):
            require(isinstance(review, dict) and set(review) == {"status", "reviewer_id", "reviewed_at", "approval_ref"},
                    "Review attestation fields are invalid")
            require(review["status"] == "APPROVED" and token(review["reviewer_id"]) and token(review["approval_ref"]),
                    "Privacy and incident-group reviews must be approved")
            timestamp(review["reviewed_at"])
            require(datetime.fromisoformat(review["reviewed_at"].replace('Z','+00:00')) >= datetime.fromisoformat(collected.replace('Z','+00:00')),
                    "Privacy/group review predates collection")
        cid = "real-" + pseudonym(key, "source", row["source_id"])
        fingerprint = pseudonym(key, "text", norm)
        require(cid not in seen_ids and fingerprint not in seen_texts, "Duplicate source or normalized report; reconcile upstream")
        seen_ids.add(cid)
        seen_texts.add(fingerprint)
        # Conservative near-duplicate quarantine across distinct incident groups.
        words = set(norm.split())
        for prior in result:
            previous = set(normalized(prior["text"]).split())
            if min(len(words), len(previous)) >= 10 and len(words & previous) / len(words | previous) >= 0.9:
                require(prior["group_id"] == pseudonym(key, "incident", row["incident_id"]),
                        "Near-duplicate reports need common incident-group adjudication")
        result.append({"case_id": cid, "group_id": pseudonym(key, "incident", row["incident_id"]),
                       "content_fingerprint": fingerprint, "text": text, "origin": row["source_kind"],
                       "collected_at": collected, "authority_ref": row["authority_ref"],
                       "privacy_review": row["privacy_review"], "group_review": row["group_review"]})
    return result


def adjudicate(cases: list[dict], annotations: list[dict], decisions: list[dict], policy: dict) -> list[dict]:
    by_case = {c["case_id"]: c for c in cases}
    require(len(by_case) == len(cases), "Duplicate case IDs")
    votes = defaultdict(list)
    for annotation in annotations:
        require(set(annotation) == {"case_id", "case_revision_sha256", "annotator_id", "policy_version", "policy_sha256", "label", "blind_to_predictions", "annotated_at"},
                "Annotation fields are invalid")
        require(annotation["case_id"] in by_case and token(annotation["annotator_id"]), "Invalid annotation reference")
        require(annotation['case_revision_sha256']==case_revision(by_case[annotation['case_id']]), "Annotation refers to a different source revision")
        require(annotation["policy_version"] == policy["version"] and annotation['policy_sha256']==policy_hash(policy)
                and annotation["blind_to_predictions"] is True,
                "Annotations must be blinded and use one policy version")
        timestamp(annotation["annotated_at"])
        require(datetime.fromisoformat(annotation["annotated_at"].replace('Z','+00:00')) >=
                datetime.fromisoformat(by_case[annotation["case_id"]]["privacy_review"]["reviewed_at"].replace('Z','+00:00')),
                "Annotation predates privacy approval")
        validate_label(annotation["label"])
        votes[annotation["case_id"]].append(annotation)
    resolved = {}
    for decision in decisions:
        require(set(decision) == {"case_id", "case_revision_sha256", "adjudicator_id", "policy_version", "policy_sha256", "annotation_hashes", "label", "reason", "adjudicated_at", "rationale"},
                "Adjudication fields are invalid")
        cid = decision["case_id"]
        require(cid in by_case and cid not in resolved, "Missing or duplicate adjudication reference")
        require(decision['case_revision_sha256']==case_revision(by_case[cid]), "Adjudication refers to a different source revision")
        pair = votes[cid]
        require(len(pair) == 2 and len({v["annotator_id"] for v in pair}) == 2, "Exactly two independent annotators are required")
        require(token(decision["adjudicator_id"]) and decision["adjudicator_id"] not in {v["annotator_id"] for v in pair},
                "Adjudicator must be independent of both annotators")
        require(decision["policy_version"] == policy["version"] and decision['policy_sha256']==policy_hash(policy)
                and sorted(decision["annotation_hashes"]) == sorted(map(digest, pair)),
                "Adjudication must reference exact annotation revisions")
        timestamp(decision["adjudicated_at"])
        require(all(datetime.fromisoformat(decision["adjudicated_at"].replace('Z','+00:00')) >=
                    datetime.fromisoformat(a["annotated_at"].replace('Z','+00:00')) for a in pair), "Adjudication predates annotations")
        screen_text(decision["rationale"])
        validate_label(decision["label"])
        agree = pair[0]["label"] == pair[1]["label"]
        require(decision["reason"] == ("AGREEMENT" if agree else "RESOLVED_DISAGREEMENT"), "Adjudication reason does not match votes")
        require(not agree or decision["label"] == pair[0]["label"], "Agreement cannot be silently relabeled")
        resolved[cid] = {**by_case[cid], "label": decision["label"], "policy_version": policy["version"],
                         "policy_sha256":policy_hash(policy),"annotation_hashes": decision["annotation_hashes"],
                         "annotations": pair, "adjudication": decision}
    require(set(resolved) == set(by_case) and set(votes) == set(by_case), "Every case needs complete blinded adjudication")
    return [resolved[cid] for cid in sorted(resolved)]


def split_cases(cases: list[dict], seed: str) -> dict[str, list[dict]]:
    require(token(seed), "A recorded split seed is required")
    parts = {name: [] for name in SPLITS}
    groups = {}
    for case in cases:
        value = int(digest([seed, case["group_id"]]), 16) % 100
        split = "development" if value < 50 else "calibration" if value < 70 else "validation" if value < 85 else "test"
        groups.setdefault(case["group_id"], split)
        parts[split].append(case)
    return parts


SOURCE_FIELDS={'case_id','group_id','content_fingerprint','text','origin','collected_at','authority_ref','privacy_review','group_review'}


def case_revision(case: dict) -> str:
    return digest({k:case[k] for k in SOURCE_FIELDS})


def reserve_splits(cases: list[dict], seed: str, *, custodian_ref: str, approval_ref: str) -> dict:
    require(cases and all(set(c)==SOURCE_FIELDS for c in cases), "Reserve partitions before annotation or model exposure")
    require(token(custodian_ref) and token(approval_ref), "Sampling-plan and custodian references are required")
    partitions=split_cases(cases,seed)
    reservation={'status':'RESERVED_BEFORE_LABELING','source_commitment':digest(sorted(cases,key=lambda c:c['case_id'])),
                 'seed':seed,'custodian_ref':custodian_ref,'approval_ref':approval_ref,
                 'assignments':{c['case_id']:s for s,rows in partitions.items() for c in rows}}
    reservation['reservation_id']=digest(reservation)
    return reservation


def build_bundle(out: Path, cases: list[dict], policy: dict, seed: str, *, custodian_ref: str, reservation: dict) -> dict:
    require(cases and token(custodian_ref), "Adjudicated records and custodian reference required")
    require(all(c.get("policy_version") == policy["version"] and "adjudication" in c for c in cases), "Unadjudicated dataset")
    for case in cases:
        screen_text(case["text"])
    adjudicate(cases, [a for c in cases for a in c["annotations"]], [c["adjudication"] for c in cases], policy)
    sources=[{k:c[k] for k in SOURCE_FIELDS} for c in cases]
    require(reservation.get('reservation_id')==digest({k:v for k,v in reservation.items() if k!='reservation_id'})
            and reservation['status']=='RESERVED_BEFORE_LABELING' and reservation['seed']==seed
            and reservation['custodian_ref']==custodian_ref
            and reservation['source_commitment']==digest(sorted(sources,key=lambda c:c['case_id'])),
            "Dataset differs from the pre-annotation split reservation")
    parts = split_cases(cases, seed)
    require({c['case_id']:s for s,rows in parts.items() for c in rows}==reservation['assignments'], "Reserved assignments changed")
    require(all(parts.values()), "Every partition needs independent groups; collect more data without searching split seeds")
    out = private_target(out)
    require(not out.exists(), "Dataset output already exists")
    files = {}
    for split, rows in parts.items():
        directory = "sealed_test" if split == "test" else "work"
        relative = f"{directory}/{split}.jsonl"
        write_new(out / relative, lines(rows))
        files[relative] = file_hash(out / relative)
    inputs=lines([{'case_id':c['case_id'],'text':c['text']} for c in parts['test']])
    write_new(out/'sealed_test/inputs.jsonl',inputs)
    files['sealed_test/inputs.jsonl']=file_hash(out/'sealed_test/inputs.jsonl')
    write_json(out / "policy.json", policy)
    files["policy.json"] = file_hash(out / "policy.json")
    origins = sorted({c["origin"] for c in cases})
    manifest = {"protocol_version": "batch2-1", "status": "RESEARCH_ONLY",
                "origins": origins, "policy_sha256": policy_hash(policy), "split_seed": seed,
                "custodian_ref": custodian_ref, "files": files,
                "reservation_id":reservation['reservation_id'],"sampling_approval_ref":reservation['approval_ref'],
                "counts": {s: len(r) for s, r in parts.items()},
                "group_counts": {s: len({c['group_id'] for c in r}) for s, r in parts.items()},
                "test_committed_sha256": files["sealed_test/test.jsonl"],
                "test_inputs_sha256": files['sealed_test/inputs.jsonl'],
                "policy_approved": bool(approved(policy)),
                "content_commitment": digest(sorted(c["content_fingerprint"] for c in cases))}
    manifest["dataset_id"] = digest(manifest)
    write_json(out / "manifest.json", manifest)
    return manifest
