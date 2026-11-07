"""Opt-in authenticated read-only tool API. Search uses the user's JWT, not root."""
import asyncio
import httpx
from typing import Any,Literal

from fastapi import APIRouter,Depends,Header,HTTPException
from pydantic import BaseModel,ConfigDict
from supabase import ClientOptions,create_client

from ..auth import Caller,current_caller
from ..config import Settings,get_settings
from .core import RetrievalError
from .tools import KnowledgeTools,TOOL_DEFINITIONS

router=APIRouter(prefix='/api/knowledge',tags=['knowledge'])


@router.get('/tools')
def definitions(caller: Caller=Depends(current_caller),cfg: Settings=Depends(get_settings)):
    if not cfg.knowledge_enabled: raise HTTPException(503,'Knowledge tools disabled')
    return {'tools':TOOL_DEFINITIONS,'max_calls_per_request':1,'synthetic_documents_only':True}


def user_client(authorization: str=Header(default=''),caller: Caller=Depends(current_caller),
                cfg: Settings=Depends(get_settings)):
    if not cfg.knowledge_enabled: raise HTTPException(503,'Knowledge tools disabled')
    if not authorization.lower().startswith('bearer ') or not authorization.split(' ',1)[1].strip():
        raise HTTPException(401,'Missing bearer token')
    # current_caller validates the token. PostgreSQL rechecks current profile and
    # membership on every RPC; no identity or department argument is accepted.
    return create_client(cfg.supabase_url,cfg.supabase_publishable_key,
        options=ClientOptions(headers={'Authorization':authorization},auto_refresh_token=False,
            persist_session=False,postgrest_client_timeout=3,
            httpx_client=httpx.Client(timeout=3,trust_env=False,follow_redirects=False)))


class ToolRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tool: Literal['campus_policies','departments','availability','ticket_history']
    arguments: dict[str,Any]


def _execute(client,request,observability_enabled=False):
    try:
        return KnowledgeTools(client,max_calls=1,observability_enabled=observability_enabled).call(request.tool,request.arguments)
    finally:
        # Request-local user JWT clients are never cached or shared between users.
        session=getattr(getattr(client,'options',None),'httpx_client',None)
        if session is not None: session.close()


@router.post('/tools')
async def invoke(request: ToolRequest,client=Depends(user_client),cfg: Settings=Depends(get_settings)):
    try:
        return await asyncio.wait_for(asyncio.to_thread(_execute,client,request,cfg.observability_enabled),timeout=8)
    except RetrievalError as error:
        raise HTTPException(422,str(error)) from None
    except TimeoutError:
        # Read-only background threads may finish later; no result is returned.
        return {'status':'ABSTAIN','reason':'RETRIEVAL_UNAVAILABLE','sources':[]}
