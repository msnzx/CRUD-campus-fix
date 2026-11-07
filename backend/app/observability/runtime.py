"""Best-effort metadata-only hooks; telemetry must not change ticket workflows."""
import logging

log=logging.getLogger('campusfix.observability')


def configure(cfg):
    try:
        from .otlp import configure as configure_export
        configure_export(cfg)
    except Exception: log.warning('Local exporter configuration rejected; memory-only tracing retained')
    if not getattr(cfg,'observability_enabled',False): return
    try:
        from .health import configure_worker,reset
        from .monitor import reset as reset_monitor
        reset_monitor()
        reset()  # a new lifespan cannot inherit a previous loop's success
        configure_worker('classification',enabled=cfg.ai_enabled,
                         stale_after_seconds=2*cfg.ollama_timeout_seconds+67)
        configure_worker('safety_retry',enabled=cfg.admin_enabled,stale_after_seconds=210)
        configure_worker('notifications',enabled=cfg.email_enabled,stale_after_seconds=max(240,3*cfg.notify_interval_seconds))
        configure_worker('vision',enabled=False)
    except Exception: log.warning('Local monitoring configuration failed')


def pulse(cfg,worker):
    if not getattr(cfg,'observability_enabled',False): return
    try:
        from .health import record_heartbeat
        record_heartbeat(worker)
    except Exception: log.warning('Local monitoring update failed')


def outcome(cfg,worker,success):
    if not getattr(cfg,'observability_enabled',False): return
    try:
        from .health import record_outcome
        record_outcome(worker,success)
    except Exception: log.warning('Local monitoring update failed')


def observed_call(cfg,name,fn,*args):
    if not getattr(cfg,'observability_enabled',False): return fn(*args)
    try:
        from .tracing import start_trace
        span=start_trace(name)
    except Exception:
        return fn(*args)
    with span:
        result=fn(*args)
        try: span.set_attribute('outcome',getattr(result,'routing_status','complete'))
        except Exception: log.warning('Local trace metadata update failed; application result preserved')
        return result


def observed_operation(enabled,name,kind,fn,*args):
    """Failure-isolated typed span for bounded read-only tools and retrieval."""
    if not enabled: return fn(*args)
    try:
        from .tracing import start_trace
        span=start_trace(name,kind)
    except Exception: return fn(*args)
    with span:
        result=fn(*args)
        try:
            if isinstance(result,list): span.set_attribute('retrieval.document_count',len(result))
            elif isinstance(result,dict): span.set_attribute('outcome',result.get('status','complete'))
        except Exception: pass
        return result
