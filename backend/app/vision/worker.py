"""Optional mock-only shadow worker using independent database leases.

Not started by production lifespan. There is deliberately no live provider,
SDK import, enabled flag, model default, user endpoint or paid network path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import Callable

from .attachments import load_all
from .contract import PROVENANCE,VisionFailure,fuse,interpret,request

log=logging.getLogger('campusfix.vision')


@dataclass(frozen=True)
class MockClaudeProvider:
    """Trusted test injection only. Every run is marked MOCK, never real evidence."""
    respond: Callable[[dict],dict]


def rpc(admin,name,**params):
    result=admin.rpc(name,params).execute().data
    if not isinstance(result,dict): raise VisionFailure('DATABASE_RESPONSE')
    return result


def _provider_response(provider,payload,observability_enabled):
    if not observability_enabled: return provider.respond(payload)
    from ..observability.tracing import start_trace,SpanKind
    from ..observability.registry import GLOBAL_PROMPT_REGISTRY,PromptVersion
    from .contract import SYSTEM,PIPELINE_VERSION
    try:
        GLOBAL_PROMPT_REGISTRY.register(PromptVersion('vision_mock',PIPELINE_VERSION,SYSTEM,'Mock-only shadow system instructions'))
        span=start_trace('vision_mock',SpanKind.LLM)
        span.set_attribute('llm.model_name','MOCK-NO-MODEL-CALL')
        span.set_attribute('pipeline.version',PIPELINE_VERSION)
    except Exception: return provider.respond(payload)
    with span: return provider.respond(payload)


def process_claim(admin,claim: dict,loader,provider: MockClaudeProvider,*,observability_enabled=False) -> dict:
    if type(provider) is not MockClaudeProvider: raise VisionFailure('PAID_VISION_BLOCKED')
    start=time.monotonic();images=[];result=None;extra={}
    error=None;retry=False
    try:
        images=load_all(loader,claim['attachments'],claim['ticket_id'])
        result,extra=interpret(_provider_response(provider,request([i.encoded for i in images],claim['text']),observability_enabled),len(images))
    except VisionFailure as failure:
        error,retry=failure.code,failure.retryable
    except (TimeoutError,ConnectionError):
        error,retry='PROVIDER_UNAVAILABLE',True
    except Exception:
        error,retry='WORKER_FAILURE',True
    fusion=fuse(claim.get('text_issue'),claim.get('text_urgent',False),result,failure=error)
    # Failures are recorded as codes, with no raw output, report text or file paths.
    provenance={**PROVENANCE,**extra,'images':[i.provenance for i in images],
                'latency_ms':int((time.monotonic()-start)*1000)}
    return rpc(admin,'vision_shadow_finish',p_ticket=claim['ticket_id'],p_token=claim['token'],
               p_findings={'vision':result,'fusion':fusion},p_provenance=provenance,
               p_error=error,p_retry=retry)


async def tick(admin,cfg,loader,provider,*,cursor=0,worker_id='offline-fixture-worker',timeout_seconds=60) -> dict:
    # Gate before any database or attachment access, including discovery.
    if cfg.vision_mode!='shadow': return {'status':'DISABLED','cursor':cursor}
    if type(provider) is not MockClaudeProvider: return {'status':'POLICY_BLOCKED','cursor':cursor}
    if not 0<timeout_seconds<=90: raise VisionFailure('WORKER_CONFIG')
    discovered=await asyncio.to_thread(rpc,admin,'vision_shadow_discover',p_after=cursor,p_limit=50)
    claim=await asyncio.to_thread(rpc,admin,'vision_shadow_claim',p_worker=worker_id,p_lease_seconds=120,p_provenance=PROVENANCE)
    if not claim.get('claimed'): return {'status':'IDLE','cursor':discovered['cursor']}
    try:
        result=await asyncio.wait_for(asyncio.to_thread(process_claim,admin,claim,loader,provider,observability_enabled=getattr(cfg,'observability_enabled',False)),timeout_seconds)
    except TimeoutError:
        # A Python thread cannot be killed. Fence its token before letting a
        # later attempt start; a late thread's finish then returns LEASE_LOST.
        result=await asyncio.to_thread(rpc,admin,'vision_shadow_finish',p_ticket=claim['ticket_id'],
            p_token=claim['token'],p_findings=None,p_provenance=PROVENANCE,p_error='WORKER_TIMEOUT',p_retry=True)
    return {'status':result['result'],'cursor':discovered['cursor']}


async def run_loop(admin,cfg,loader,provider,*,interval=7,worker_id='offline-fixture-worker'):
    if cfg.vision_mode!='shadow' or type(provider) is not MockClaudeProvider:
        return # disabled or unapproved provider must never poll, fetch or call
    cursor=0
    while True:
        try:
            result=await tick(admin,cfg,loader,provider,cursor=cursor,worker_id=worker_id)
            cursor=result['cursor']
        except Exception:
            # Don't log exception text, URLs, report content or response payloads.
            log.warning('Vision shadow tick failed; durable lease recovery will retry')
        await asyncio.sleep(interval)
