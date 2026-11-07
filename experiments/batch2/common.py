"""Private artifact I/O and integrity checks shared by the offline workflow."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / ".campusfix-private"


class ProtocolError(ValueError):
    """Invalid or unapproved research input. Messages must not expose report text."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError) as error:
        raise ProtocolError("Invalid JSON artifact") from error


def read_jsonl(path: Path) -> list[dict]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError()
        canonical(rows)  # Reject non-finite JSON extensions too.
        return rows
    except (ValueError, UnicodeError) as error:
        raise ProtocolError("Invalid JSONL artifact") from error


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def token(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:/-]{1,100}", value) is not None


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def private_target(path: Path) -> Path:
    """Forbid overwriting source/frozen artifacts. Repo outputs are private only."""
    path = path.resolve()
    require(path != ROOT and (ROOT not in path.parents or PRIVATE in path.parents),
            "Repository outputs must be inside .campusfix-private")
    return path


def write_new(path: Path, content: bytes) -> None:
    path = private_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ProtocolError("Refusing to overwrite an existing research artifact") from error
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)


def write_json(path: Path, value: Any) -> None:
    write_new(path, canonical(value) + b"\n")


def lines(rows: list[dict]) -> bytes:
    return b"".join(canonical(row) + b"\n" for row in rows)


def verify_frozen() -> None:
    base = ROOT / "evaluation"
    manifest = read_json(base / "artifacts.sha256.json")
    for name, expected in manifest.items():
        require(file_hash(base / name) == expected, "Frozen artifact checksum mismatch")


def verify_bundle(path: Path, *, include_test=False) -> dict:
    manifest = read_json(path / "manifest.json")
    require(manifest.get("protocol_version") == "batch2-1", "Unknown dataset protocol")
    require(manifest.get("dataset_id") == digest({k: v for k, v in manifest.items() if k != "dataset_id"}),
            "Dataset manifest commitment mismatch")
    expected = {"policy.json", "sealed_test/test.jsonl", "sealed_test/inputs.jsonl",
                *[f"work/{s}.jsonl" for s in ("development", "calibration", "validation")]}
    require(set(manifest["files"]) == expected, "Dataset manifest file set is invalid")
    for relative, expected in manifest["files"].items():
        if relative.startswith("sealed_test/") and not include_test:
            continue  # Development tooling never opens independent test labels.
        target = (path / relative).resolve()
        require(path.resolve() in target.parents, "Invalid manifest path")
        require(file_hash(target) == expected, "Dataset artifact checksum mismatch")
    require(digest(read_json(path / 'policy.json')) == manifest['policy_sha256'], "Policy commitment mismatch")
    return manifest
