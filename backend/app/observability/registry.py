"""Model registry, prompt versioning, token accounting, and cost calculation.

Maintains cryptographic digests of prompt templates, active model specifications,
token rate cards, and financial cost tracking for operational monitoring.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import threading
from copy import deepcopy
import math
from typing import Any

# Pricing per 1,000,000 tokens (USD)
RATE_CARDS = {name: {"input_per_million":0.0,"output_per_million":0.0,"is_local":True}
              for name in ("deepseek-r1:8b","qwen3:8b","qwen3:14b","Qwen/Qwen2.5-1.5B-Instruct","fixture-hash-64")}
# API charges only; local compute, energy and hosting are not priced here.



@dataclass(frozen=True)
class PromptVersion:
    prompt_id: str
    version: str
    template: str
    description: str
    template_sha256: str = field(init=False)

    def __post_init__(self):
        if any(not isinstance(v,str) or not v for v in (self.prompt_id,self.version,self.template)):
            raise ValueError('Prompt identity, version and exact template are required')
        sha = hashlib.sha256(self.template.encode("utf-8")).hexdigest()
        object.__setattr__(self, "template_sha256", sha)

    def render(self, **kwargs) -> str:
        return self.template.format(**kwargs)


class PromptRegistry:
    """Registry managing versioned, content-hashed prompt templates."""

    def __init__(self):
        self._prompts: dict[str, PromptVersion] = {}
        self._lock=threading.RLock()
        self._register_defaults()

    def register(self, prompt: PromptVersion) -> None:
        key = f"{prompt.prompt_id}@{prompt.version}"
        with self._lock:
            if key in self._prompts and self._prompts[key] != prompt:
                raise ValueError('Prompt version is immutable; register a new version')
            if key not in self._prompts and len(self._prompts)>=128:
                raise ValueError('Prompt registry capacity reached')
            self._prompts[key] = prompt

    def get(self, prompt_id: str, version: str) -> PromptVersion:
        key = f"{prompt_id}@{version}"
        with self._lock:
            return self._prompts[key]

    def list_prompts(self) -> list[dict[str, Any]]:
        with self._lock:
            prompts=tuple(self._prompts.values())
        return [
            {
                "prompt_id": p.prompt_id,
                "version": p.version,
                "sha256": p.template_sha256,
                "description": p.description,
            }
            for p in prompts
        ]

    def _register_defaults(self):
        # No invented prompt may masquerade as the runtime prompt. Register
        # exact caller-supplied versioned templates when instrumentation exists.
        pass


class CostTracker:
    """Tracks token consumption and financial cost using model rate cards."""

    def __init__(self):
        self._usage: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def calculate_cost(self, model_name: str, prompt_tokens: int, completion_tokens: int) -> float | None:
        if any(type(n) is not int or n<0 for n in (prompt_tokens,completion_tokens)):
            raise ValueError('Token counts must be nonnegative integers')
        if not isinstance(model_name,str) or not 1<=len(model_name)<=128:
            raise ValueError('Invalid model reference')
        rate = RATE_CARDS.get(model_name)
        if rate is None: return None
        cost_in = (prompt_tokens / 1_000_000.0) * rate["input_per_million"]
        cost_out = (completion_tokens / 1_000_000.0) * rate["output_per_million"]
        return round(cost_in + cost_out, 6)

    def record_usage(
        self,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> dict[str, Any]:
        cost = self.calculate_cost(model_name, prompt_tokens, completion_tokens)
        with self._lock:
            if model_name not in self._usage and len(self._usage)>=128:
                raise ValueError('Cost tracker capacity reached')
            if model_name not in self._usage:
                self._usage[model_name] = {
                    "requests": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "total_cost_usd": 0.0,
                    "unknown_cost_requests": 0,
                }
            entry = self._usage[model_name]
            entry["requests"] += 1
            entry["prompt_tokens"] += prompt_tokens
            entry["completion_tokens"] += completion_tokens
            entry["total_tokens"] += (prompt_tokens + completion_tokens)
            if cost is None: entry['unknown_cost_requests']+=1
            else: entry["total_cost_usd"] = round(entry["total_cost_usd"] + cost, 6)

            return {
                "model_name": model_name,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost,
            }

    def get_summary(self) -> dict[str, Any]:
        with self._lock:
            total_cost = sum(v["total_cost_usd"] for v in self._usage.values())
            total_tokens = sum(v["total_tokens"] for v in self._usage.values())
            total_requests = sum(v["requests"] for v in self._usage.values())
            by_model=deepcopy(self._usage)
            for row in by_model.values():
                row['known_api_cost_usd']=row['total_cost_usd']
                if row['unknown_cost_requests']: row['total_cost_usd']=None
            return {
                "total_requests": total_requests,
                "total_tokens": total_tokens,
                "total_cost_usd": None if any(v['unknown_cost_requests'] for v in self._usage.values()) else round(total_cost, 6),
                "known_api_cost_usd": round(total_cost,6),
                "unknown_cost_requests":sum(v['unknown_cost_requests'] for v in self._usage.values()),
                "by_model": by_model,
            }

    def reset(self) -> None:
        with self._lock:
            self._usage.clear()


# Global singletons
GLOBAL_PROMPT_REGISTRY = PromptRegistry()
GLOBAL_COST_TRACKER = CostTracker()
