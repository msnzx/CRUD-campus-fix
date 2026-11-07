"""Bounded best-effort pattern redaction, not a guarantee of anonymization.

Tracing separately suppresses all content and allowlists metadata. Names and
indirect identifiers cannot safely be removed by regular expressions alone.
"""
from __future__ import annotations

import re
from typing import Any

# Regular expressions for sensitive patterns
PATTERNS: list[tuple[re.Pattern, str]] = [
    # JWT tokens and long secret keys
    (re.compile(r"\b(?:eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"), "[JWT_REDACTED]"),
    (re.compile(r"\b(?:sb_secret_|sk-ant-|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,})[A-Za-z0-9_\-.]{8,}\b"), "[SECRET_KEY_REDACTED]"),
    # Passwords in URLs / connection strings
    (re.compile(r"(postgres(?:ql)?://[^:]+:)([^@]+)(@)"), r"\1[PASSWORD_REDACTED]\3"),
    # Authorization header values
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9_\-.]{12,}\b"), r"\1[TOKEN_REDACTED]"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL_REDACTED]"),
    # Phone numbers (US/standard)
    (re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)"), "[PHONE_REDACTED]"),
    # Social Security Numbers (SSN)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
    # Credit Card Numbers (Luhn-like 13-19 digits)
    (re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"), "[CARD_REDACTED]"),
    # Student / Employee ID numbers (e.g. 7-9 consecutive digits with prefix)
    (re.compile(r"\b(?:student|employee|banner)?\s*(?:id|#)\s*[:#-]?\s*([0-9]{7,9})\b", re.I), "ID:[ID_REDACTED]"),
]

SENSITIVE_KEY_NAMES = {
    "authorization", "proxy-authorization", "password", "secret", "api_key",
    "apikey", "token", "access_token", "refresh_token", "service_role_key",
    "cookie", "set-cookie", "private_key"
}


def redact_string(value: str) -> str:
    """Scan and redact all sensitive patterns from a text string."""
    if not isinstance(value, str) or not value:
        return value
    redacted = value
    for pattern, replacement in PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_data(obj: Any) -> Any:
    """Return JSON-safe bounded copies; reject cycles and unsupported objects."""
    import math
    seen=set()
    def clean(value,depth=0):
        if depth>8: return '[DEPTH_LIMIT]'
        if isinstance(value,str): return redact_string(value[:4096])
        if value is None or type(value) in (bool,int): return value
        if type(value) is float: return value if math.isfinite(value) else None
        if isinstance(value,(dict,list,tuple,set)):
            if id(value) in seen: return '[CYCLE]'
            seen.add(id(value))
            try:
                if isinstance(value,dict):
                    output={}
                    for key,item in list(value.items())[:64]:
                        label=redact_string(str(key)[:128])
                        normalized=str(key).lower().replace('-','_')
                        output[label]='[REDACTED]' if any(s.replace('-','_') in normalized for s in SENSITIVE_KEY_NAMES) else clean(item,depth+1)
                    return output
                return [clean(item,depth+1) for item in list(value)[:64]]
            finally: seen.remove(id(value))
        return '[UNSUPPORTED]'
    return clean(obj)


def assert_zero_sensitive_leaks(obj: Any) -> None:
    """Verify that a data structure contains no unredacted sensitive tokens."""
    text = str(obj)
    if "eyJ" in text:
        # Check if actual JWT structure matches
        if re.search(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b", text):
            raise AssertionError("Potential unredacted JWT found in telemetry payload")
    if re.search(r"\b(?:sb_secret_|sk-ant-|sk-[A-Za-z0-9]{20,})", text):
        raise AssertionError("Potential unredacted API key found in telemetry payload")
    if re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text):
        raise AssertionError("Potential unredacted email found in telemetry payload")
    if re.search(r"\b\d{3}-\d{2}-\d{4}\b", text):
        raise AssertionError("Potential unredacted SSN found in telemetry payload")
