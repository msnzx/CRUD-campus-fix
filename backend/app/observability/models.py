"""Instrument existing model requests without changing prompts or making probes."""
from hashlib import sha256
import threading

from .registry import GLOBAL_COST_TRACKER, GLOBAL_PROMPT_REGISTRY, PromptVersion
from .tracing import start_trace, SpanKind, _label

_lock=threading.Lock()
_models={}


def model_observations():
    with _lock: return [dict(value) for value in _models.values()]


def _observe(model, success, measured):
    with _lock:
        if model not in _models and len(_models)>=128: return
        row=_models.setdefault(model,{'model':model,'requests':0,'failures':0,
            'usage_unknown':0,'weights_digest':None,'identity':'PROVIDER_REPORTED_NOT_VERIFIED'})
        row['requests']+=1;row['failures']+=int(not success);row['usage_unknown']+=int(not measured)


def observed_model_call(cfg, operation, system_prompt, call):
    """call() returns the existing Ollama response; no prompts/outputs are stored.

    Only static system instruction templates belong here, never user messages.
    Usage must come from the provider. Missing fields are unknown, never zero.
    All telemetry errors are isolated from the result and original exceptions.
    """
    if not getattr(cfg,'observability_enabled',False): return call()
    try:
        digest=sha256(system_prompt.encode('utf-8')).hexdigest()
        GLOBAL_PROMPT_REGISTRY.register(PromptVersion(operation,digest,system_prompt,'Runtime system instruction'))
        span=start_trace(operation,SpanKind.LLM)
        span.set_attribute('pipeline.version','sha256:'+digest)
        span.set_attribute('llm.model_name',cfg.ollama_model)
    except Exception:
        return call()
    with span:
        try: result=call()
        except BaseException:
            try: _observe(_label(cfg.ollama_model),False,False)
            except Exception: pass
            raise
        try:
            model=_label(cfg.ollama_model) if result.get('model',cfg.ollama_model)==cfg.ollama_model else 'provider-model-mismatch'
            span.set_attribute('llm.model_name',model)
            prompt_tokens,completion_tokens=result.get('prompt_eval_count'),result.get('eval_count')
            for key,value in (('prompt',prompt_tokens),('completion',completion_tokens)):
                span.set_attribute('llm.token_count.'+key,value)
            measured=all(type(n) is int and 0<=n<=1000000000 for n in (prompt_tokens,completion_tokens))
            if measured:
                span.set_attribute('llm.token_count.total',prompt_tokens+completion_tokens)
                GLOBAL_COST_TRACKER.record_usage(model,prompt_tokens,completion_tokens)
            _observe(model,True,measured)
        except Exception: pass
        return result
