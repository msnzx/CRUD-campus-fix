"""Common utilities, cryptographic commitments, and gates for Batch 5."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any
import unicodedata

from experiments.batch2.common import (
    ROOT, canonical, digest, file_hash, finite, lines, private_target,
    read_json, read_jsonl, require, token, verify_frozen, write_json, write_new
)
from evaluation.dev_audit.analyze_development import DEPARTMENTS, ISSUE_TYPES, ORDINAL, ISSUE_TO_DEPT


class Batch5Error(ValueError):
    """Base error for Batch 5 research protocols and gates."""


class ProvenanceError(Batch5Error):
    """Invalid or tampered staff-correction provenance."""


class AdjudicationError(Batch5Error):
    """Adjudication protocol or consensus violation."""


class ActiveLearningError(Batch5Error):
    """Active learning selection, scoring, or quota error."""


class DatasetError(Batch5Error):
    """Dataset versioning, packaging, or partition error."""


class GateBlockedError(Batch5Error):
    """Operation blocked by a mandatory data governance, safety, or policy gate."""


class ExperimentError(Batch5Error):
    """Invalid LoRA, QLoRA, or distillation experiment configuration."""


PII_PATTERNS = (
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    r"https?://\S+",
    r"\b\d{3}-\d{2}-\d{4}\b",
    r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)",
    r"\b\d{7,}\b",
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    r"\b(?:room|apt|apartment|student\s*id|ticket\s*(?:id|#))\s*[:#-]?\s*[A-Z]?\d+\b",
    r"\b(?:sk-ant-|sb_secret_|eyJ)[A-Za-z0-9_\-.]{12,}",
)


def validate_timestamp(value: str) -> str:
    """Validate timezone-aware ISO timestamp that is not in the future."""
    require(isinstance(value, str), "Timestamp required")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProvenanceError(f"Malformed timestamp: {value}")
    if dt.tzinfo is None or dt > datetime.now(timezone.utc):
        raise ProvenanceError(f"Timestamp must be timezone-aware and not in the future: {value}")
    return value


def screen_report_text(text: str, max_chars: int = 12000) -> str:
    """Validate that text is within bounds and free of obvious PII or raw secrets."""
    if not isinstance(text, str) or not (1 <= len(text.strip()) <= max_chars):
        raise ProvenanceError(f"Report text must contain 1–{max_chars} characters")
    for pattern in PII_PATTERNS:
        if re.search(pattern, text, re.I):
            raise GateBlockedError("Potential personal data or secret detected; return to privacy review")
    if any(unicodedata.category(c) in ("Cc", "Cf") and c not in "\n\r\t" for c in text):
        raise GateBlockedError("Control characters detected; privacy review required")
    return text.strip()


def normalize_text(text: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def jaccard_word_similarity(text_a: str, text_b: str) -> float:
    words_a = set(normalize_text(text_a).split())
    words_b = set(normalize_text(text_b).split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)
