"""Bounded OTLP/HTTP JSON transport to an explicitly opted-in loopback collector.

Span completion only queues metadata. Flushing is separate from application work.
No SDK defaults, proxies, redirects, DNS resolution, credentials or remote hosts.
"""
from collections import deque
from datetime import datetime
import ipaddress
import json
import threading
import time
from urllib.parse import urlsplit

import httpx


def _nano(iso):
    instant = datetime.fromisoformat(iso)
    return str(int(instant.timestamp())*1_000_000_000+instant.microsecond*1000)


def otlp_payload(spans):
    wire = []
    for span in spans:
        attrs = []
        for key,value in span['attributes'].items():
            attrs.append({'key':key,'value':{'intValue':str(value)} if type(value) is int else {'stringValue':value}})
        item = {'traceId':span['context']['trace_id'],'spanId':span['context']['span_id'],
                'name':span['name'],'kind':1,'startTimeUnixNano':_nano(span['start_time']),
                'endTimeUnixNano':_nano(span['end_time']), 'attributes':attrs,
                'status':{'code':2 if span['status']['status_code']=='ERROR' else
                          1 if span['status']['status_code']=='OK' else 0}}
        if span['parent_id']: item['parentSpanId']=span['parent_id']
        wire.append(item)
    return {'resourceSpans':[{'resource':{'attributes':[{'key':'service.name','value':{'stringValue':'campusfix-backend'}}]},
                            'scopeSpans':[{'scope':{'name':'campusfix.metadata','version':'1'},'spans':wire}]}]}


class LocalOTLPExporter:
    def __init__(self,endpoint_url='http://127.0.0.1:6006/v1/traces',timeout=2.0,*,allow_local=False,transport=None,capacity=256):
        parsed=urlsplit(endpoint_url)
        try: loopback=ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError: loopback=False
        if not allow_local or not loopback or parsed.scheme!='http' or parsed.path!='/v1/traces' or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise RuntimeError('Only explicitly enabled numeric loopback OTLP collectors are allowed')
        if not 0<timeout<=5 or type(capacity) is not int or not 1<=capacity<=2000:
            raise ValueError('Invalid OTLP limits')
        self.endpoint=endpoint_url;self.timeout=timeout;self.transport=transport;self.capacity=capacity
        self._queue=deque();self._lock=threading.Lock();self._flush_lock=threading.Lock()
        self._retry_at=0;self._consecutive_failures=0
        self._stats={'accepted':0,'rejected':0,'dropped':0,'failures':0,'pending':0,'last_error':None}

    def export(self,span):
        data=span.to_dict()  # reapply metadata allowlist at boundary
        with self._lock:
            if len(self._queue)>=self.capacity:
                self._stats['dropped']+=1;return
            self._queue.append(data)

    def stats(self):
        with self._lock: return {**self._stats,'pending':len(self._queue)}

    def flush(self):
        if not self._flush_lock.acquire(blocking=False): return
        try:
            with self._lock: batch=list(self._queue)[:64]
            if not batch: return
            if time.monotonic()<self._retry_at: return
            rejected=0;permanent=False;error=None;delay=None
            try:
                with httpx.Client(timeout=self.timeout,transport=self.transport,trust_env=False,follow_redirects=False) as client:
                    with client.stream('POST',self.endpoint,json=otlp_payload(batch),headers={'Content-Type':'application/json'}) as response:
                        if response.status_code!=200:
                            permanent=response.status_code not in (429,502,503,504)
                            header=response.headers.get('Retry-After','')
                            if header.isdigit(): delay=min(int(header),300)
                            raise ValueError('Collector HTTP failure')
                        body=bytearray()
                        for chunk in response.iter_bytes(chunk_size=4096):
                            body.extend(chunk)
                            if len(body)>16384:
                                permanent=True;raise ValueError('Oversized collector response')
                permanent=True  # malformed success messages cannot justify replay
                result=json.loads(body)
                if not isinstance(result,dict): raise ValueError('Invalid OTLP response')
                partial=result.get('partialSuccess',{})
                if not isinstance(partial,dict): raise ValueError('Invalid OTLP partial success')
                rejected=int(partial.get('rejectedSpans',0))
                if not 0<=rejected<=len(batch): raise ValueError('Invalid rejected count')
                error='PARTIAL_REJECTION' if rejected else 'COLLECTOR_WARNING' if partial.get('errorMessage') else None
            except Exception:
                with self._lock:
                    self._stats['failures']+=1
                    self._stats['last_error']='PERMANENT_EXPORT_FAILURE' if permanent else 'COLLECTOR_UNAVAILABLE'
                    if permanent:
                        for _ in batch: self._queue.popleft()
                        self._stats['dropped']+=len(batch)
                    else:
                        self._consecutive_failures+=1
                        self._retry_at=time.monotonic()+(delay if delay is not None else min(60,2**min(self._consecutive_failures,6)))
                return  # retryable batches retained; backoff never blocks span finish
            self._consecutive_failures=0;self._retry_at=0
            with self._lock:
                for _ in batch: self._queue.popleft()
                self._stats['accepted']+=len(batch)-rejected
                self._stats['rejected']+=rejected
                self._stats['last_error']=error
                # OTLP partial success must not be retried: accepted subset unknown.
        finally: self._flush_lock.release()


_configured=None
_configuration_error=None


def configure(cfg):
    global _configured,_configuration_error
    _configured = None;_configuration_error=None
    if not getattr(cfg,'observability_enabled',False): return
    try:
        _configured = (LocalOTLPExporter(cfg.observability_otlp_endpoint,allow_local=True)
                       if getattr(cfg,'observability_exporter','memory')=='otlp-local' else None)
    except Exception:
        _configuration_error='CONFIGURATION_REJECTED'
        raise


def queue_configured(span):
    if _configured is not None: _configured.export(span)


def flush_configured():
    if _configured is not None: _configured.flush()


def export_status():
    return {'mode':'memory','configuration_error':_configuration_error} if _configured is None else {'mode':'otlp-local',**_configured.stats()}
