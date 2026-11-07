"""Bounded process-local worker progress; importing this module performs no I/O.

A heartbeat proves a loop reached an observation point, not that work succeeded.
Backlog is unknown until measured; there is no implied database liveness probe.
"""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time

_lock=threading.RLock()
_export_lock=threading.Lock()
_heartbeat={}
_monotonic={}
_outcomes={}
_expected={}
_backlog_counts={}
ROOT=Path(__file__).resolve().parents[3]


def _label(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,63}',value):
        raise ValueError('Invalid monitoring label')
    return value


def configure_worker(worker_id,*,enabled=True,stale_after_seconds=120):
    _label(worker_id)
    if type(enabled) is not bool or type(stale_after_seconds) not in (int,float) or not math.isfinite(stale_after_seconds) or stale_after_seconds<=0:
        raise ValueError('Invalid worker configuration')
    with _lock:
        if worker_id not in _expected and len(_expected)>=64: raise ValueError('Worker limit reached')
        _expected[worker_id]={'enabled':enabled,'stale_after_seconds':stale_after_seconds}


def record_heartbeat(worker_id):
    _label(worker_id)
    with _lock:
        if worker_id not in _expected: configure_worker(worker_id)
        _heartbeat[worker_id]=time.time()
        _monotonic[worker_id]=time.monotonic()


def record_outcome(worker_id,success):
    if type(success) is not bool: raise ValueError('Invalid worker outcome')
    with _lock:
        record_heartbeat(worker_id)
        _outcomes[worker_id]={'success':success,'observed_at':_heartbeat[worker_id]}


def get_heartbeat(worker_id):
    with _lock: return _heartbeat.get(worker_id)


def set_backlog(queue_name,count):
    _label(queue_name)
    if type(count) is not int or not 0<=count<=9223372036854775807: raise ValueError('Backlog must be a bounded nonnegative integer')
    with _lock:
        if queue_name not in _backlog_counts and len(_backlog_counts)>=32: raise ValueError('Queue limit reached')
        _backlog_counts[queue_name]={'count':count,'observed_at':time.time()}


def get_backlog(queue_name):
    with _lock:
        row=_backlog_counts.get(queue_name)
        return row['count'] if row else None


def snapshot():
    with _lock:
        workers={}
        now=time.monotonic()
        for worker,config in _expected.items():
            age=max(0,now-_monotonic[worker]) if worker in _monotonic else None
            outcome=_outcomes.get(worker)
            state='DISABLED' if not config['enabled'] else 'UNKNOWN' if age is None else (
                'STALE' if age>config['stale_after_seconds'] else
                'ALIVE' if outcome is None else 'HEALTHY' if outcome['success'] else 'DEGRADED')
            workers[worker]={**config,'state':state,'age_seconds':age,'last_outcome':deepcopy(outcome)}
        return {'scope':'PROCESS_LOCAL','durable':False,'heartbeat':dict(_heartbeat),
                'workers':workers,'backlog':{k:v['count'] for k,v in _backlog_counts.items()},
                'backlog_observations':deepcopy(_backlog_counts),
                'backlog_status':'OBSERVED' if _backlog_counts else 'UNKNOWN'}


def export_snapshot(directory=None):
    base=Path(directory or os.getenv('CAMPUSFIX_PRIVATE',str(ROOT/'.campusfix-private'))).resolve()
    private=(ROOT/'.campusfix-private').resolve()
    if base==ROOT or (ROOT in base.parents and base!=private and private not in base.parents):
        raise ValueError('Health exports must use private storage')
    directory=base/'batch6'
    directory.mkdir(parents=True,exist_ok=True)
    target=directory/'health_snapshot.json'
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix='health-',suffix='.tmp',dir=directory,delete=False) as stream:
            temporary=Path(stream.name)
            json.dump(snapshot(),stream,allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        # Windows cannot reliably replace one destination concurrently, even
        # when each writer has a distinct temp file. Serialize local publishes.
        with _export_lock: os.replace(temporary,target)
    finally:
        if temporary is not None and temporary.exists(): temporary.unlink()
    return target


def health_report(): return snapshot()


def reset():
    """Test/process lifecycle reset; no persisted data is removed."""
    with _lock:
        for state in (_heartbeat,_monotonic,_outcomes,_expected,_backlog_counts): state.clear()
