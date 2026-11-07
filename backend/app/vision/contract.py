"""Bounded structured Claude response interpretation and observational fusion."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from . import validation
from .prompts_v1 import SYSTEM_PROMPT, build_content

PIPELINE_VERSION = 'vision-shadow-1.1'
MODEL = 'UNAPPROVED-CLAUDE-MODEL'
SCHEMA = deepcopy(validation.load_schema())
SCHEMA['$id'] = 'campusfix/vision-shadow/1.1'
SCHEMA['properties']['schema_version']['enum'] = ['1.1']
SCHEMA['properties']['multiple_issues_detected'] = {'type':'boolean'}
SCHEMA['required'].append('multiple_issues_detected')
SYSTEM = SYSTEM_PROMPT + (
    '\nShadow contract 1.1: return schema_version 1.1 and an explicit '
    'multiple_issues_detected boolean, including different issues in one photo. '
    'Multiple issues require outcome abstain with reason ambiguous or conflicting_images. '
    'Preserve visible hazards even when abstaining. This result is observational only.')


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


PROVENANCE = {'pipeline_version':PIPELINE_VERSION,'prompt_sha256':sha(SYSTEM.encode()),
              'schema_sha256':sha(canonical(SCHEMA)),'provider':'MOCK','requested_model':MODEL,
              'code_sha256':sha(b''.join(p.name.encode()+b'\0'+p.read_bytes()
                                       for p in sorted(Path(__file__).parent.glob('*.py'))))}


class VisionFailure(Exception):
    """Only fixed error codes cross the worker boundary; never raw provider text."""
    def __init__(self,code: str,retryable=False):
        super().__init__(code)
        self.code,self.retryable=code,retryable


def request(images: list[tuple[str,str]], text: str) -> dict:
    if not images or len(images)>4 or len(text)>16000:
        raise VisionFailure('INPUT_LIMIT')
    return {'model':MODEL,'max_tokens':4096,'system':SYSTEM,
            'messages':[{'role':'user','content':build_content(images,text)}],
            'output_config':{'format':{'type':'json_schema','schema':validation.schema_for_api(SCHEMA)}}}


def _bounded(value,depth=0) -> bool:
    if depth>8: return False
    if isinstance(value,str): return len(value)<=2000
    if isinstance(value,list): return len(value)<=32 and all(_bounded(v,depth+1) for v in value)
    if isinstance(value,dict): return len(value)<=24 and all(_bounded(v,depth+1) for v in value.values())
    return value is None or type(value) in (bool,int)


def interpret(response: dict,n_images: int) -> tuple[dict,dict]:
    """Messages-style plain mapping; no SDK import or raw response persistence."""
    if type(n_images) is not int or not 1<=n_images<=4: raise VisionFailure('ATTACHMENT_COUNT')
    if not isinstance(response,dict): raise VisionFailure('RESPONSE_INVALID')
    if response.get('model') != MODEL: raise VisionFailure('MODEL_MISMATCH')
    stop=response.get('stop_reason')
    if stop=='refusal': raise VisionFailure('REFUSAL')
    if stop=='max_tokens': raise VisionFailure('TRUNCATED')
    if stop!='end_turn': raise VisionFailure('UNEXPECTED_STOP')
    content=response.get('content')
    if not isinstance(content,list) or len(content)!=1 or not isinstance(content[0],dict) or content[0].get('type')!='text':
        raise VisionFailure('RESPONSE_INVALID')
    text=content[0].get('text')
    if not isinstance(text,str) or not text or len(text)>64000: raise VisionFailure('OUTPUT_LIMIT')
    def pairs(items):
        obj={}
        for key,value in items:
            if key in obj: raise ValueError()
            obj[key]=value
        return obj
    try:
        result=json.loads(text,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not _bounded(result): raise ValueError()
    except (ValueError,RecursionError): raise VisionFailure('JSON_INVALID') from None
    if validation.validate(result,SCHEMA): raise VisionFailure('SCHEMA_INVALID')
    if validation.semantic_errors(result,n_images): raise VisionFailure('SEMANTIC_INVALID')
    if result['multiple_issues_detected'] and not (
        result['outcome']=='abstain' and result['abstain_reason'] in ('ambiguous','conflicting_images')):
        raise VisionFailure('SEMANTIC_INVALID')
    # No OCR, narrative, evidence, filenames, locations, identities or raw exceptions.
    minimal={k:result[k] for k in ('schema_version','outcome','abstain_reason','issue_type','confidence',
                                  'visible_hazards','safety_escalation_suggested','cross_image_consistency',
                                  'multiple_issues_detected')}
    minimal['image_quality']=[{'image_index':a['image_index'],'usable':a['usable'],
                               'quality_problems':a['quality_problems']} for a in result['image_assessments']]
    usage=response.get('usage',{})
    if not isinstance(usage,dict): raise VisionFailure('USAGE_INVALID')
    tokens={k:usage.get(k,0) for k in ('input_tokens','output_tokens')}
    if any(type(v) is not int or not 0<=v<=1000000 for v in tokens.values()): raise VisionFailure('USAGE_INVALID')
    return minimal,{'served_model':MODEL,'response_sha256':sha(text.encode()),'usage':tokens}


def fuse(text_issue: str | None,text_urgent: bool,result: dict | None,*,failure: str | None=None) -> dict:
    """Observations only: never supplies an effective route, priority or alert."""
    relation='UNASSESSED'
    reasons=[]
    if failure: reasons.append('VISION_UNAVAILABLE')
    elif result is not None:
        if result['multiple_issues_detected']: reasons.append('MULTIPLE_ISSUES')
        if result['outcome']=='abstain': reasons.append('ABSTENTION')
        if result['outcome']=='no_issue_visible': reasons.append('NO_ISSUE_VISIBLE')
        if result['visible_hazards']: reasons.append('HAZARD_OBSERVED')
        if result['confidence']!='high': reasons.append('UNCERTAIN')
        if result['issue_type'] is not None:
            relation='NO_TEXT_BASELINE' if text_issue in (None,'Other') else (
                'AGREEMENT' if text_issue==result['issue_type'] else 'DISAGREEMENT')
            if relation=='DISAGREEMENT': reasons.append('TEXT_IMAGE_DISAGREEMENT')
    return {'mode':'SHADOW_MOCK','relation':relation,'human_review_required':True,
            'review_reasons':reasons,'text_urgent_observed':bool(text_urgent),
            'vision_hazard_observed':bool(result and result['visible_hazards']),
            'routing_applied':False,'notifications_created':False}
