"""Paired fixture-only retrieval benchmark. No generation or model execution."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import time

HERE=Path(__file__).parent
VARIANTS={'lexical':('LEXICAL',False),'vector':('VECTOR',False),
          'hybrid':('HYBRID',False),'hybrid_reranked':('HYBRID',True)}


def fixtures():
    manifest=json.loads((HERE/'fixtures.sha256.json').read_text())
    raw=(HERE/'fixtures.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['fixtures.json']: raise ValueError('Retrieval fixture integrity mismatch')
    return json.loads(raw)


def evaluate(factory,department_ids):
    """factory(actor,mode,rerank,audit) returns a request-scoped KnowledgeTools.

    audit records candidate/read source URIs before reranking, so masking a leak
    at final output cannot make the permission metric pass.
    """
    data=fixtures();results={}
    for variant,(mode,rerank) in VARIANTS.items():
        records=[]
        for case in data['cases']:
            audit=[];tool=factory(case['actor'],mode,rerank,audit)
            args={'department_id':department_ids[case['department']]} if case.get('tool')=='availability' else {'query':case['query'],'limit':5}
            start=time.perf_counter();out=tool.call(case.get('tool','campus_policies'),args)
            latency=(time.perf_counter()-start)*1000
            ranked=[r['citation']['source_uri'].split('://',1)[1] for r in out.get('sources',[]) if 'citation' in r]
            relevant=set(case['relevant']);allowed=set(data['allowed_sources'][case['actor']])
            unique=list(dict.fromkeys(ranked));hits=[i+1 for i,s in enumerate(unique) if s in relevant]
            dcg=sum(1/math.log2(i+1) for i in hits)
            ideal=sum(1/math.log2(i+2) for i in range(min(5,len(relevant))))
            observed=ranked+[s.split('://',1)[1] for s in audit]
            records.append({'case_id':case['id'],'returned':ranked,'status':out['status'],
                'recall_at_5':len(set(ranked)&relevant)/len(relevant) if relevant else None,
                'reciprocal_rank':1/min(hits) if hits else 0,'ndcg_at_5':dcg/ideal if ideal else None,
                'abstention_correct':(out['status']=='ABSTAIN')==(not bool(relevant)),
                'unauthorized_exposures':sum(s not in allowed for s in observed),'latency_ms':latency})
        answerable=[r for r in records if r['recall_at_5'] is not None]
        results[variant]={'cases':len(records),'answerable':len(answerable),
            'recall_at_5':sum(r['recall_at_5'] for r in answerable)/len(answerable),
            'mrr':sum(r['reciprocal_rank'] for r in answerable)/len(answerable),
            'ndcg_at_5':sum(r['ndcg_at_5'] for r in answerable)/len(answerable),
            'abstention_accuracy':sum(r['abstention_correct'] for r in records)/len(records),
            'unauthorized_exposures':sum(r['unauthorized_exposures'] for r in records),
            'mean_latency_ms':sum(r['latency_ms'] for r in records)/len(records),'records':records}
    from app.knowledge.core import MODEL,REVISION
    return {'status':'SYNTHETIC_SOFTWARE_BENCHMARK','real_world_accuracy':False,'production_promotion':False,
        'fixture_sha256':hashlib.sha256((HERE/'fixtures.json').read_bytes()).hexdigest(),
        'embedding_model':MODEL,'embedding_revision':REVISION,'variants':results,
        'code_sha256':hashlib.sha256(b''.join(p.name.encode()+p.read_bytes() for p in sorted(
            (HERE.parents[1]/'backend/app/knowledge').glob('*.py')))).hexdigest()}


class AuditClient:
    def __init__(self,client,audit): self.client,self.audit=client,audit
    def rpc(self,name,params):
        call=self.client.rpc(name,params);audit=self.audit
        class Call:
            def execute(self):
                response=call.execute()
                for row in response.data:
                    if 'source_uri' in row: audit.append(row['source_uri'])
                return response
        return Call()
