"""Offline CLI: imports reviewed artifacts; never runs inference or loads .env."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from . import dataset, benchmark, protocol
from .common import ProtocolError, digest, lines, read_json, read_jsonl, write_json, write_new
from .policy import proposal, policy_hash


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('plan','ingest','reserve','annotation-template','adjudication-template','build','fit','score','curve','compare','register','test-inputs','final-score'):
        p=sub.add_parser(command)
        p.add_argument('--out',type=Path,required=True)
        if command=='ingest': p.add_argument('--records',type=Path,required=True)
        if command=='reserve':
            p.add_argument('--cases',type=Path,required=True)
            p.add_argument('--seed',required=True)
            p.add_argument('--custodian',required=True)
            p.add_argument('--approval-ref',required=True)
        if command in ('annotation-template','adjudication-template','build'): p.add_argument('--cases',type=Path,required=True)
        if command=='annotation-template': p.add_argument('--annotator',required=True)
        if command in ('adjudication-template','build'): p.add_argument('--annotations',type=Path,required=True)
        if command=='adjudication-template': p.add_argument('--adjudicator',required=True)
        if command=='build':
            p.add_argument('--reservation',type=Path,required=True)
            p.add_argument('--adjudications',type=Path,required=True)
            p.add_argument('--seed',required=True)
            p.add_argument('--custodian',required=True)
        if command in ('fit','score','curve','register','final-score'):
            p.add_argument('--bundle',type=Path,required=True)
            p.add_argument('--predictions',type=Path,required=True)
            p.add_argument('--run',type=Path,required=True)
        if command in ('curve','register','final-score'): p.add_argument('--calibrator',type=Path,required=True)
        if command in ('curve','register'): p.add_argument('--thresholds',type=float,nargs='+',required=True)
        if command=='register':
            p.add_argument('--criteria',type=Path,required=True)
            p.add_argument('--approval-ref',required=True)
            p.add_argument('--custodian',required=True)
        if command=='final-score': p.add_argument('--registration',type=Path,required=True)
        if command=='test-inputs':
            p.add_argument('--bundle',type=Path,required=True)
            p.add_argument('--registration',type=Path,required=True)
        if command=='compare':
            p.add_argument('--bundle',type=Path,required=True)
            p.add_argument('--candidates',type=Path,required=True,help='Private JSON list of run/prediction artifact paths')
            p.add_argument('--split',choices=['development','validation'],default='validation')
        if command=='score':
            p.add_argument('--split',choices=['development','validation'],required=True)
            p.add_argument('--calibrator',type=Path)
            p.add_argument('--threshold',type=float)
        if command in ('annotation-template','adjudication-template','build'): p.add_argument('--policy',type=Path)
    args=parser.parse_args(argv)
    try:
        policy=read_json(args.policy) if getattr(args,'policy',None) else proposal()
        if args.command=='plan': result=protocol.experiment_plan()
        elif args.command=='compare':
            runs=[{'run':read_json(Path(r['run'])),'predictions':read_jsonl(Path(r['predictions']))} for r in read_json(args.candidates)]
            result=benchmark.compare_candidates(args.bundle,runs,args.split)
        elif args.command=='test-inputs':
            protocol.release_test_inputs(args.bundle,read_json(args.registration),args.out)
            print('Released independent inputs without labels; selection is frozen.')
            return 0
        elif args.command=='ingest':
            key=os.environ.get('CAMPUSFIX_DATASET_HMAC_KEY','').encode()
            result=dataset.ingest(read_jsonl(args.records),key)
        elif args.command=='reserve':
            result=dataset.reserve_splits(read_jsonl(args.cases),args.seed,custodian_ref=args.custodian,approval_ref=args.approval_ref)
        elif args.command=='annotation-template':
            result=[{'case_id':c['case_id'],'text':c['text'],'annotation':{
                'case_id':c['case_id'],'case_revision_sha256':dataset.case_revision(c),'annotator_id':args.annotator,'policy_version':policy['version'],
                'policy_sha256':policy_hash(policy),
                'blind_to_predictions':True,'annotated_at':None,'label':None}} for c in read_jsonl(args.cases)]
        elif args.command=='adjudication-template':
            cases=read_jsonl(args.cases)
            annotations=read_jsonl(args.annotations)
            result=[{'case_id':c['case_id'],'text':c['text'],
                     'adjudication':{'case_id':c['case_id'],'case_revision_sha256':dataset.case_revision(c),'adjudicator_id':args.adjudicator,
                        'policy_version':policy['version'],'annotation_hashes':[digest(a) for a in annotations if a['case_id']==c['case_id']],
                        'policy_sha256':policy_hash(policy),
                        'label':None,'reason':None,'rationale':None,'adjudicated_at':None}}
                    for c in cases]
        elif args.command=='build':
            cases=dataset.adjudicate(read_jsonl(args.cases),read_jsonl(args.annotations),read_jsonl(args.adjudications),policy)
            result=dataset.build_bundle(args.out,cases,policy,args.seed,custodian_ref=args.custodian,reservation=read_json(args.reservation))
            print('Created private candidate dataset; no model was evaluated.')
            return 0
        else:
            predictions,run=read_jsonl(args.predictions),read_json(args.run)
            calibrator=read_json(args.calibrator) if getattr(args,'calibrator',None) else None
            if args.command=='fit': result=benchmark.fit_calibrator(args.bundle,predictions,run)
            elif args.command=='score': result=benchmark.score_work(args.bundle,args.split,predictions,run,calibrator=calibrator,threshold=args.threshold)
            elif args.command=='curve': result=benchmark.threshold_curve(args.bundle,predictions,run,calibrator,args.thresholds)
            elif args.command=='register': result=protocol.preregister(args.bundle,predictions,run,calibrator,args.thresholds,read_json(args.criteria),approval_ref=args.approval_ref,custodian_ref=args.custodian)
            else: result=protocol.final_score(args.bundle,predictions,run,calibrator,read_json(args.registration))
        if args.command in ('ingest','annotation-template','adjudication-template'):
            write_new(args.out,lines(result))
        else:
            write_json(args.out,result)
        print('Wrote private research artifact; no inference or production promotion performed.')
        return 0
    except ProtocolError as error:
        print(str(error),file=sys.stderr)
        return 2
    except Exception:
        print('Invalid or unavailable protocol input; review private artifacts locally.',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
