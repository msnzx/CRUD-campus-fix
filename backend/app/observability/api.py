"""Feature-gated diagnostics using existing verified JWT and database role lookup."""
from fastapi import APIRouter,Depends,HTTPException
from ..auth import Caller,current_caller
from ..config import Settings,get_settings

router=APIRouter(prefix='/api/observability',tags=['observability'])


def enabled_settings(cfg: Settings=Depends(get_settings)):
    if not cfg.observability_enabled: raise HTTPException(503,'Observability disabled')
    if not cfg.admin_enabled: raise HTTPException(503,'Administrative authentication unavailable')
    return cfg


def system_admin(cfg: Settings=Depends(enabled_settings),caller: Caller=Depends(current_caller)):
    if not caller.is_system_admin: raise HTTPException(403,'System admins only')
    return caller


@router.get('/health')
def health(caller: Caller=Depends(system_admin)):
    from .health import health_report
    from .monitor import database_health
    from .otlp import export_status
    return {**health_report(), 'database':database_health(), 'exporter':export_status()}


@router.get('/traces')
def traces(caller: Caller=Depends(system_admin)):
    from .tracing import GLOBAL_TRACE_EXPORTER
    return GLOBAL_TRACE_EXPORTER.get_spans()


@router.get('/models')
def models(caller: Caller=Depends(system_admin)):
    from .models import model_observations
    from .registry import GLOBAL_COST_TRACKER,GLOBAL_PROMPT_REGISTRY
    return {'models':model_observations(),'prompts':GLOBAL_PROMPT_REGISTRY.list_prompts(),
            'measured_usage':GLOBAL_COST_TRACKER.get_summary()}
