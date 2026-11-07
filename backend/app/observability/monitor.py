"""Read actual aggregate queues and persist observed progress, never ticket content.

The separate loop cannot manufacture worker heartbeats. Missing schema, database
failure and stale samples stay visible; they cannot stop ticket processing.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import logging
import threading
import time
import uuid

from . import health

log = logging.getLogger(__name__)
_lock = threading.Lock()
_sample = None
_sample_clock = None
_failed = False


def reset():
    global _sample, _sample_clock, _failed
    with _lock:
        _sample, _sample_clock, _failed = None, None, False


def database_health():
    with _lock:
        age = None if _sample_clock is None else max(0, time.monotonic()-_sample_clock)
        return {'status': 'UNAVAILABLE' if _failed else 'UNKNOWN' if age is None else
                'STALE' if age > 60 else 'FRESH', 'age_seconds': age,
                'last_observation': deepcopy(_sample)}


def sample_once(admin, instance_id):
    global _sample, _sample_clock, _failed
    try:
        local = health.snapshot()
        rows = []
        for worker, observation in local['workers'].items():
            timestamp = local['heartbeat'].get(worker)
            rows.append({'instance_id': str(instance_id), 'worker': worker,
                         'enabled': observation['enabled'], 'state': observation['state'],
                         'progress_at': datetime.fromtimestamp(timestamp,timezone.utc).isoformat() if timestamp else None,
                         'observed_at': datetime.now(timezone.utc).isoformat()})
        if rows:
            admin.table('observability_workers').upsert(rows,on_conflict='instance_id,worker').execute()
        data = admin.rpc('observability_snapshot',{}).execute().data
        # Projection is fixed by SQL; reject malformed replies rather than false health.
        if not isinstance(data,dict) or set(data)!={'observed_at','classification','safety','email','workers','workers_truncated'}:
            raise ValueError('Invalid monitoring projection')
        with _lock:
            _sample, _sample_clock, _failed = deepcopy(data), time.monotonic(), False
        return data
    except Exception:
        with _lock: _failed = True
        raise


async def monitor_loop(cfg):
    from supabase import create_client, ClientOptions
    import httpx
    from .otlp import flush_configured
    global _failed
    instance_id = uuid.uuid4()  # no machine name, PID, user identity or ticket IDs
    transport = httpx.Client(timeout=3,trust_env=False,follow_redirects=False)
    try:
        admin = None
        while True:
            try:
                if admin is None:
                    admin = create_client(cfg.supabase_url,cfg.supabase_service_role_key,
                        options=ClientOptions(httpx_client=transport,postgrest_client_timeout=3))
                await asyncio.to_thread(sample_once,admin,instance_id)
            except Exception:
                with _lock: _failed = True
                log.warning('Database monitoring unavailable; previous sample retained')
            try: await asyncio.to_thread(flush_configured)
            except Exception: log.warning('Local telemetry flush failed; worker processing preserved')
            await asyncio.sleep(cfg.observability_interval_seconds)
    finally:
        transport.close()
