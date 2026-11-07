"""Bounded metadata-only spans with optional queued loopback OTLP export.

Content capture and external export are disabled. Loopback export is opt-in.
"""
from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
import secrets
import threading
import time
import math
import re
from typing import Any, Callable

from .redaction import redact_data,redact_string

log = logging.getLogger("campusfix.tracing")
_CONTENT_KEYS={'input.value','output.value','tool.parameters','tool.output','llm.invocation_parameters'}
_COUNT_KEYS={'llm.token_count.prompt','llm.token_count.completion','llm.token_count.total','retrieval.document_count'}
_LABEL_KEYS={'llm.model_name','tool.name','input.mime_type','output.mime_type','pipeline.version','outcome'}


def _label(value):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:/-]{0,99}',value) and redact_string(value)==value else 'operation'


class SpanKind(str, Enum):
    CHAIN = "CHAIN"
    LLM = "LLM"
    RETRIEVER = "RETRIEVER"
    TOOL = "TOOL"
    AGENT = "AGENT"
    GUARDRAIL = "GUARDRAIL"


class StatusCode(str, Enum):
    OK = "OK"
    ERROR = "ERROR"
    UNSET = "UNSET"


def generate_trace_id() -> str:
    """Generate 16-byte (32-hex) OpenTelemetry trace ID."""
    return secrets.token_hex(16)


def generate_span_id() -> str:
    """Generate 8-byte (16-hex) OpenTelemetry span ID."""
    return secrets.token_hex(8)


class Span:
    """Represents a single timed unit of AI execution."""

    def __init__(
        self,
        name: str,
        span_kind: SpanKind = SpanKind.CHAIN,
        trace_id: str | None = None,
        parent_id: str | None = None,
        exporter: TraceExporter | None = None,
    ):
        self.name = _label(name)
        self.span_kind = span_kind
        self.trace_id = trace_id or generate_trace_id()
        self.span_id = generate_span_id()
        self.parent_id = parent_id
        self.start_time = time.perf_counter()
        self.start_time_iso = datetime.now(timezone.utc).isoformat()
        self.end_time: float | None = None
        self.end_time_iso: str | None = None
        self.duration_ms: float = 0.0
        self.status = StatusCode.OK
        self.status_message: str | None = None
        self.attributes: dict[str, Any] = {
            "openinference.span.kind": span_kind.value,
        }
        self.events: list[dict[str, Any]] = []
        self._exporter = exporter
        self._finished = False
        self._finish_lock = threading.Lock()
        self._context_token = None

    def set_attribute(self, key: str, value: Any) -> Span:
        """Set an attribute on the span, ensuring sensitive values are redacted."""
        if key in _CONTENT_KEYS:
            self.attributes[key]='[REDACTED]'
        elif key in _COUNT_KEYS and type(value) is int and 0<=value<=1000000000:
            self.attributes[key]=value
        elif key in _LABEL_KEYS:
            self.attributes[key]=_label(value)
        # Unknown attributes are omitted, including their possibly sensitive keys.
        return self

    def set_status(self, status: StatusCode, message: str | None = None) -> Span:
        self.status = status
        if message:
            self.status_message = 'ERROR_DETAILS_SUPPRESSED'
        return self

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        if len(self.events)>=32: return
        self.events.append({
            "name": _label(name),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attributes": {},  # free-form event payloads are not telemetry metadata
        })

    def finish(self) -> None:
        with self._finish_lock:
            if self._finished: return
            self.end_time = time.perf_counter()
            self.end_time_iso = datetime.now(timezone.utc).isoformat()
            self.duration_ms = max(0,(self.end_time - self.start_time) * 1000.0)
            self._finished = True
        if self._exporter:
            try: self._exporter.export(self)
            except Exception: log.warning('Local trace export failed; application work preserved')

    def to_dict(self) -> dict[str, Any]:
        """Return local diagnostics, not a validated OTLP/Phoenix wire payload."""
        # Public attributes may be mutated by instrumentation: enforce the same
        # allowlist again at the storage boundary, not just in set_attribute.
        safe=Span('snapshot',self.span_kind if isinstance(self.span_kind,SpanKind) else SpanKind.CHAIN)
        for key,value in list(self.attributes.items())[:32]: safe.set_attribute(key,value)
        return {
            "name": _label(self.name),
            "context": {
                "trace_id": self.trace_id if isinstance(self.trace_id,str) and re.fullmatch('[a-f0-9]{32}',self.trace_id) else None,
                "span_id": self.span_id if isinstance(self.span_id,str) and re.fullmatch('[a-f0-9]{16}',self.span_id) else None,
            },
            "parent_id": self.parent_id if isinstance(self.parent_id,str) and re.fullmatch('[a-f0-9]{16}',self.parent_id) else None,
            "start_time": self.start_time_iso,
            "end_time": self.end_time_iso or self.start_time_iso,
            "duration_ms": self.duration_ms,
            "status": {
                "status_code": self.status.value,
                "description": 'ERROR_DETAILS_SUPPRESSED' if self.status_message else None,
            },
            "attributes": safe.attributes,
            "events": [{"name":_label(e.get('name')),"attributes":{}} for e in self.events[:32]],
        }

    def __enter__(self) -> Span:
        self._context_token = _ACTIVE_SPAN.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if exc_type is not None: self.set_status(StatusCode.ERROR,'ERROR')
            self.finish()
        finally:
            if self._context_token is not None: _ACTIVE_SPAN.reset(self._context_token)


# Context variable for ambient span tracking
_ACTIVE_SPAN: ContextVar[Span | None] = ContextVar("active_span", default=None)


class TraceExporter:
    """Base interface for exporting trace spans."""

    def export(self, span: Span) -> None:
        raise NotImplementedError


class InMemoryTraceExporter(TraceExporter):
    """Thread-safe in-memory buffer storing completed spans for offline testing."""

    def __init__(self, max_spans: int = 2000):
        if type(max_spans) is not int or not 1<=max_spans<=2000: raise ValueError('Invalid trace capacity')
        self._spans: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.max_spans = max_spans

    def export(self, span: Span) -> None:
        with self._lock:
            if len(self._spans) >= self.max_spans:
                self._spans.pop(0)
            self._spans.append(deepcopy(span.to_dict()))
        from .otlp import queue_configured
        queue_configured(span)

    def get_spans(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._spans)

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()


from .otlp import LocalOTLPExporter


class PhoenixHTTPExporter(LocalOTLPExporter, TraceExporter):
    """OTLP/HTTP JSON, explicitly opted-in numeric loopback collector only."""


# Global default in-memory exporter
GLOBAL_TRACE_EXPORTER = InMemoryTraceExporter()


def start_trace(
    name: str,
    span_kind: SpanKind = SpanKind.CHAIN,
    exporter: TraceExporter | None = None,
) -> Span:
    """Start a new root or child span."""
    parent = _ACTIVE_SPAN.get()
    trace_id = parent.trace_id if parent else None
    parent_id = parent.span_id if parent else None
    return Span(
        name=name,
        span_kind=span_kind,
        trace_id=trace_id,
        parent_id=parent_id,
        exporter=exporter or GLOBAL_TRACE_EXPORTER,
    )


# Helper functions to populate standard OpenInference attributes
def record_llm_span(
    span: Span,
    *,
    model_name: str,
    prompt: str,
    output: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    invocation_params: dict[str, Any] | None = None,
) -> None:
    span.set_attribute("llm.model_name", model_name)
    span.set_attribute("input.value", prompt)
    span.set_attribute("input.mime_type", "text/plain")
    span.set_attribute("output.value", output)
    span.set_attribute("output.mime_type", "text/plain")
    span.set_attribute("llm.token_count.prompt", prompt_tokens)
    span.set_attribute("llm.token_count.completion", completion_tokens)
    if all(type(n) is int and 0<=n<=1000000000 for n in (prompt_tokens,completion_tokens)):
        span.set_attribute("llm.token_count.total", prompt_tokens + completion_tokens)
    if invocation_params:
        span.set_attribute("llm.invocation_parameters", invocation_params)


def record_retrieval_span(
    span: Span,
    *,
    query: str,
    documents: list[dict[str, Any]],
) -> None:
    span.set_attribute("input.value", query)
    if isinstance(documents,list): span.set_attribute('retrieval.document_count',len(documents))


def record_tool_span(
    span: Span,
    *,
    tool_name: str,
    parameters: dict[str, Any],
    result: Any,
) -> None:
    span.set_attribute("tool.name", tool_name)
    span.set_attribute("tool.parameters", parameters)
    span.set_attribute("tool.output", result)
