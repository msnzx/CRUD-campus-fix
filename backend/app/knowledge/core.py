"""Offline embedding and reranking contracts; deterministic fixture implementation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib
import json
import math
import re
import unicodedata
import uuid

DIMENSIONS=64
MODEL='fixture-hash-64'
REVISION=hashlib.sha256(b'campusfix:token-sha256-signed-count-l2:64:v1').hexdigest()
MAX_CANDIDATES=20
MAX_RESULTS=5
STOPWORDS={'the','a','an','to','for','of','is','are','how','what','when','where','can','i','and','do','does','in','on','with'}


class RetrievalError(ValueError):
    pass


def require(ok,message):
    if not ok: raise RetrievalError(message)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def tokens(text: str) -> list[str]:
    return re.findall(r'[a-z0-9]+',unicodedata.normalize('NFKC',text).casefold())


def query_text(text,max_length=500):
    require(isinstance(text,str) and 1<=len(text.strip())<=max_length,'Invalid query bounds')
    require(not any(unicodedata.category(c) in ('Cc','Cf') for c in text),'Invalid query characters')
    require(bool(tokens(text)),'Query has no searchable terms')
    return text.strip()


def bounded_int(value,maximum):
    require(type(value) is int and 1<=value<=maximum,'Invalid numeric bounds')
    return value


def vector(values) -> list[float]:
    require(isinstance(values,(tuple,list)) and len(values)==DIMENSIONS,'Embedding dimension mismatch')
    require(all(type(x) in (int,float) and math.isfinite(x) for x in values),'Invalid embedding values')
    norm=math.sqrt(sum(x*x for x in values))
    require(math.isfinite(norm) and norm>0,'Invalid embedding norm')
    return [x/norm for x in values]


class FixtureEmbedding:
    """Signed token feature hashing, NOT a trained semantic model or probability."""
    model_id=MODEL
    revision=REVISION
    dimensions=DIMENSIONS
    local_only=True

    def embed(self,text: str) -> list[float]:
        query_text(text,max_length=2000)
        values=[0.0]*DIMENSIONS
        for token in tokens(text):
            key=hashlib.sha256(token.encode()).digest()
            values[int.from_bytes(key[:4],'big')%DIMENSIONS]+=1 if key[4]&1 else -1
        return vector(values)


INJECTION=re.compile(r'ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|'
    r'<\s*/?\s*(?:system|assistant|tool)\b|\b(?:system|developer)\s+(?:message|prompt)\s*:|'
    r'\b(?:reveal|exfiltrate)\s+(?:the\s+)?(?:secrets|credentials|tokens)|'
    r'\b(?:execute|call)\s+(?:the\s+)?(?:shell|sql|tool)\b',re.I)


def suspicious(text):
    return bool(INJECTION.search(unicodedata.normalize('NFKC',text)))


def rerank(query,candidates,limit=5):
    """Local deterministic coverage reranker over already-authorized candidates.

    A vector-only hash collision is not supporting evidence. No score is claimed
    to be a probability; real thresholds need a real relevance benchmark.
    """
    terms=set(tokens(query))-STOPWORDS;ranked=[]
    if not terms: return []
    for row in candidates:
        if suspicious(row['content']+' '+row['title']+' '+row['section']): continue
        overlap=len(terms & set(tokens(row['content'])))
        if not overlap: continue
        coverage=overlap/len(terms)
        if coverage<0.34: continue
        score=coverage+(0.1 if query.casefold() in row['content'].casefold() else 0)
        ranked.append((score,float(row['hybrid_score']),row['chunk_id'],row))
    ranked.sort(key=lambda x:(-x[0],-x[1],x[2]))
    result=[];seen=set()
    for score,hybrid,cid,row in ranked:
        key=(row['document_id'],row['section'])
        if key in seen: continue
        seen.add(key);result.append(dict(row,rerank_score=score))
        if len(result)>=limit: break
    return result


@dataclass(frozen=True)
class FixtureDocument:
    source_id: str
    title: str
    text: str
    audience: str='CAMPUS'
    department_id: int | None=None
    kind: str='POLICY'
    version: int=1
    effective_at: str='2020-01-01T00:00:00+00:00'
    expires_at: str='2099-01-01T00:00:00+00:00'


def prepare_fixture(document: FixtureDocument,embedder=None) -> tuple[dict,list[dict]]:
    """No scraping, file loaders, real-document ingestion or automatic approval."""
    require(type(document) is FixtureDocument,'Only explicit synthetic fixture ingestion is supported')
    embedder=embedder or FixtureEmbedding()
    require(type(embedder) is FixtureEmbedding,'Only fixture embeddings can be indexed in this batch')
    require(bool(re.fullmatch(r'[a-z][a-z0-9-]{1,60}',document.source_id)),'Invalid fixture source ID')
    require(document.title.startswith('SYNTHETIC FIXTURE:') and len(document.title)<=200,'Fixture title required')
    require(isinstance(document.text,str) and document.text.startswith('SYNTHETIC FIXTURE:')
            and 1<=len(document.text)<=40000,'Fixture content required')
    require(document.audience in ('CAMPUS','STAFF','DEPARTMENT') and document.kind in ('POLICY','AVAILABILITY'),'Invalid fixture scope')
    require(document.department_id is None or (type(document.department_id) is int and document.department_id>0),'Invalid department')
    require(document.audience!='DEPARTMENT' or document.department_id is not None,'Department scope requires membership target')
    require(document.kind!='AVAILABILITY' or document.department_id is not None,'Availability department required')
    require(type(document.version) is int and document.version>0,'Invalid document version')
    try:
        start=datetime.fromisoformat(document.effective_at);end=datetime.fromisoformat(document.expires_at)
        require(start.tzinfo is not None and end.tzinfo is not None and end>start,'Invalid effective interval')
    except (ValueError,TypeError): raise RetrievalError('Invalid effective interval') from None
    source_uri='campusfix-fixture://'+document.source_id
    doc_id=str(uuid.uuid5(uuid.NAMESPACE_URL,source_uri+':'+str(document.version)))
    metadata={'document_id':doc_id,'version':document.version,'title':document.title,'source_uri':source_uri,
              'source_sha256':sha(document.text),'origin':'SYNTHETIC','approval_ref':None,'publication':'PUBLISHED',
              'kind':document.kind,'audience':document.audience,'department_id':document.department_id,
              'effective_at':document.effective_at,'expires_at':document.expires_at}
    # Overlapping character windows retain stable exact source offsets and hashes.
    chunks=[]
    for ordinal,start in enumerate(range(0,len(document.text),1000)):
        content=document.text[start:start+1200]
        chunk_id=str(uuid.uuid5(uuid.UUID(doc_id),str(ordinal)+':'+sha(content)))
        chunks.append({'chunk_id':chunk_id,'document_id':doc_id,'ordinal':ordinal,'section':f'chars:{start}:{start+len(content)}',
            'content':content,'content_sha256':sha(content),'embedding':embedder.embed(content),
            'embedding_model':embedder.model_id,'embedding_revision':embedder.revision,
            'quarantined':suspicious(document.text+' '+document.title)})
    return metadata,chunks


def citation(row):
    return {k:row[k] for k in ('chunk_id','document_id','version','title','source_uri','source_sha256',
                               'content_sha256','section','origin','expires_at')}
