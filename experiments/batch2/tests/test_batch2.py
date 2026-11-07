"""Offline protocol tests. Every created report/prediction is a unit fixture.

Temporary fixture scores are tests of software, never model accuracy evidence.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.batch2 import benchmark as b, dataset as d, policy as p, protocol
from experiments.batch2.__main__ import main
from experiments.batch2.common import ROOT, ProtocolError, canonical, digest, read_json, read_jsonl, verify_bundle, write_json

KEY=b'unit-fixture-only-never-a-real-key-000000000'


def source(i=0, **overrides):
    review={'status':'APPROVED','reviewer_id':'fixture-reviewer','reviewed_at':'2020-01-02T00:00:00Z','approval_ref':'TEST-only'}
    row={'source_id':f'fixture-source-{i}','incident_id':f'fixture-incident-{i}','source_kind':'test_fixture',
         'text':f'Unit fixture observation marker{i} repair request', 'collected_at':'2020-01-01T00:00:00Z',
         'authority_ref':'TEST-only','privacy_review':deepcopy(review),'group_review':deepcopy(review)}
    return dict(row,**overrides)


def label(urgent=False,review=False):
    return {'issue_type':'Other' if review else 'Plumbing','department':None if review else 'Facilities',
            'priority':'URGENT' if urgent else 'MEDIUM','action':'NEEDS_REVIEW' if review else 'AUTO_ROUTE',
            'safety_escalation':urgent,'review_reason':'no_matching_category' if review else None}


def fixture_policy(approved=False):
    policy=p.proposal()
    if approved:
        policy.update(status='APPROVED',approved_by='TEST-only',approval_ref='TEST-only')
        for decision in policy['decisions'].values():
            decision.update(status='APPROVED',approval_ref='TEST-only',resolution='UNIT FIXTURE ONLY; no real approval')
    return policy


def annotated(cases,policy):
    votes,decisions=[],[]
    for i,case in enumerate(cases):
        truth=label(urgent=(i%3==0))
        pair=[{'case_id':case['case_id'],'case_revision_sha256':d.case_revision(case),'annotator_id':reviewer,'policy_version':policy['version'],
               'policy_sha256':p.policy_hash(policy),
               'label':deepcopy(truth),'blind_to_predictions':True,'annotated_at':'2020-01-03T00:00:00Z'}
              for reviewer in ('fixture-labeler-a','fixture-labeler-b')]
        votes.extend(pair)
        decisions.append({'case_id':case['case_id'],'case_revision_sha256':d.case_revision(case),'adjudicator_id':'fixture-adjudicator','policy_version':policy['version'],
                          'policy_sha256':p.policy_hash(policy),
                          'annotation_hashes':list(map(digest,pair)),'label':deepcopy(truth),'reason':'AGREEMENT',
                          'adjudicated_at':'2020-01-04T00:00:00Z','rationale':'Unit test fixture agreement only.'})
    return votes,decisions


def run_for(manifest,split='validation',**overrides):
    return dict({'candidate_id':'fixture-model-a','model_name':'fixture:8b','model_digest':'a'*64,
                 'prompt_sha256':'b'*64,'prompt_version':'fixture-v1','adapter_version':'fixture-batch1',
                 'code_sha256':'c'*64,'decoding':{'temperature':0,'think':False,'stream':False,'seed':None},
                 'score_kind':'uncalibrated_score','environment':{'python':'fixture','ollama_version':'NOT_RUN','hardware':'fixture'},
                 'partition_sha256':manifest['files'][f'work/{split}.jsonl'] if split!='test' else manifest['test_committed_sha256']},**overrides)


def predictions(cases, calibration=False):
    result=[]
    for i,c in enumerate(cases):
        truth=c['label']
        row={'case_id':c['case_id'],'status':'ok','issue_type':truth['issue_type'],'department':truth['department'],
             'priority':truth['priority'],'action':truth['action'],'safety_signal':truth['safety_escalation'],
             'confidence_score':0.9,'latency_seconds':1.0,'error_kind':None}
        if calibration:
            row['confidence_score']=0.1+0.8*i/max(1,len(cases)-1)
            if i < len(cases)//2:
                row.update(issue_type='Lighting',department='Facilities')
        result.append(row)
    return result


class PrivacyTests(unittest.TestCase):
    def test_rejects_unit_fixtures_in_real_ingestion(self):
        with self.assertRaises(ProtocolError): d.ingest([source()],KEY)

    def test_pseudonyms_are_keyed_and_raw_ids_not_exported(self):
        result=d.ingest([source()],KEY,allow_test_fixtures=True)
        serialized=canonical(result).decode()
        self.assertNotIn('fixture-source-0',serialized)
        self.assertNotIn('fixture-incident-0',serialized)
        self.assertNotIn(KEY.decode(),serialized)
        self.assertNotEqual(result[0]['case_id'],d.ingest([source()],b'x'*32,allow_test_fixtures=True)[0]['case_id'])

    def test_privacy_and_group_approval_are_required(self):
        for field in ('privacy_review','group_review'):
            row=source();row[field]['status']='PENDING'
            with self.subTest(field=field),self.assertRaises(ProtocolError): d.ingest([row],KEY,allow_test_fixtures=True)

    def test_rejects_common_pii_and_never_echoes_it(self):
        for secret in ('person@example.test','(973)618-3259','123-45-6789','Room 314','https://private.example/token',
                       '123456789','10.20.30.40','12 Private Street','sb_secret_'+('x'*30)):
            with self.subTest(kind=secret[:4]),self.assertRaises(ProtocolError) as error:
                d.ingest([source(text='Contact '+secret)],KEY,allow_test_fixtures=True)
            self.assertNotIn(secret,str(error.exception))

    def test_unknown_input_fields_and_short_keys_are_rejected(self):
        for row,key in ((dict(source(),email='private'),KEY),(source(),b'short')):
            with self.assertRaises(ProtocolError): d.ingest([row],key,allow_test_fixtures=True)

    def test_duplicate_ids_and_normalized_content_rejected(self):
        for second in (source(),source(1,text=' UNIT fixture observation marker0 repair request!! ')):
            with self.assertRaises(ProtocolError): d.ingest([source(),second],KEY,allow_test_fixtures=True)

    def test_near_duplicates_need_common_incident_group(self):
        text='Unit fixture alpha beta gamma delta epsilon zeta eta theta iota kappa lambda'
        with self.assertRaises(ProtocolError):
            d.ingest([source(text=text),source(1,text=text+' extra')],KEY,allow_test_fixtures=True)

    def test_frozen_ids_and_content_cannot_enter_new_corpus(self):
        with self.assertRaises(ProtocolError): d.ingest([source(source_id='CFB-0001')],KEY,allow_test_fixtures=True)
        # Programmatic integrity screening only; this text is never shown or used for tuning.
        import csv
        with (ROOT/'evaluation/campusfix_benchmark_v2.csv').open(encoding='utf-8-sig') as stream:
            text=next(csv.DictReader(stream))['text']
        with self.assertRaises(ProtocolError): d.ingest([source(text=text)],KEY,allow_test_fixtures=True)

    def test_timestamps_and_control_characters_are_checked(self):
        for update in ({'collected_at':'2999-01-01T00:00:00Z'},{'collected_at':'2020-01-01'},
                       {'collected_at':'2021-01-01T00:00:00Z'},{'text':'hidden\u200bcharacters'}):
            with self.assertRaises(ProtocolError): d.ingest([source(**update)],KEY,allow_test_fixtures=True)


class PolicyAndLabelsTests(unittest.TestCase):
    def setUp(self):
        self.policy=fixture_policy()
        self.cases=d.ingest([source()],KEY,allow_test_fixtures=True)
        self.votes,self.decisions=annotated(self.cases,self.policy)

    def test_proposal_is_not_approval_and_does_not_adopt_old_labels(self):
        self.assertFalse(p.approved(p.proposal()))
        self.assertEqual(len(self.policy['decisions']),23)
        self.assertTrue(all(v['status']=='PENDING' for v in self.policy['decisions'].values()))

    def test_approval_retains_original_decision_questions_and_lineage(self):
        for field in ('question','lineage'):
            policy=fixture_policy(True)
            if field=='question': policy['decisions']['U-01']['question']='Changed unit fixture question'
            else: policy.pop('prior_proposal_sha256')
            self.assertFalse(p.approved(policy))

    def test_other_urgent_can_remain_review(self):
        p.validate_label(label(urgent=True,review=True))

    def test_unknown_priority_requires_review_and_auto_other_is_invalid(self):
        bad=label();bad['priority']=None
        with self.assertRaises(ProtocolError): p.validate_label(bad)
        bad=label(review=True);bad['action']='AUTO_ROUTE'
        with self.assertRaises(ProtocolError): p.validate_label(bad)

    def test_labeling_does_not_accept_model_labels_or_unblinded_votes(self):
        self.votes[0]['blind_to_predictions']=False
        with self.assertRaises(ProtocolError): d.adjudicate(self.cases,self.votes,self.decisions,self.policy)

    def test_two_distinct_annotators_and_independent_adjudicator_required(self):
        for change in ('duplicate','adjudicator','missing'):
            votes,decisions=annotated(self.cases,self.policy)
            if change=='duplicate': votes[1]['annotator_id']=votes[0]['annotator_id']
            elif change=='adjudicator': decisions[0]['adjudicator_id']=votes[0]['annotator_id']
            else: votes.pop()
            with self.assertRaises(ProtocolError): d.adjudicate(self.cases,votes,decisions,self.policy)

    def test_adjudication_binds_exact_votes_and_policy_version(self):
        for change in ('label','policy'):
            votes,decisions=annotated(self.cases,self.policy)
            if change=='label': votes[0]['label']=label()
            else: decisions[0]['policy_version']='other-policy'
            with self.assertRaises(ProtocolError): d.adjudicate(self.cases,votes,decisions,self.policy)

    def test_disagreements_require_explicit_resolution(self):
        self.votes[1]['label']=label()
        self.decisions[0]['annotation_hashes']=list(map(digest,self.votes))
        with self.assertRaises(ProtocolError): d.adjudicate(self.cases,self.votes,self.decisions,self.policy)
        self.decisions[0]['reason']='RESOLVED_DISAGREEMENT'
        result=d.adjudicate(self.cases,self.votes,self.decisions,self.policy)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['annotations'],self.votes)

    def test_agreement_cannot_be_silently_relabeled(self):
        self.decisions[0]['label']=label()
        with self.assertRaises(ProtocolError): d.adjudicate(self.cases,self.votes,self.decisions,self.policy)

    def test_approval_cannot_omit_decisions_or_change_contract(self):
        approved=fixture_policy(True)
        self.assertTrue(p.approved(approved))
        approved['decisions'].pop('U-01')
        self.assertFalse(p.approved(approved))

    def test_policy_approval_or_resolution_changes_require_new_annotations(self):
        changed=deepcopy(self.policy)
        changed['decisions']['U-01']['resolution']='Unit fixture resolution only.'
        with self.assertRaises(ProtocolError): d.adjudicate(self.cases,self.votes,self.decisions,changed)
        approved=fixture_policy(True);approved['contract']['priority']='invented'
        self.assertFalse(p.approved(approved))

    def test_changed_report_or_provenance_invalidates_old_labels(self):
        for field,value in (('text','Unit fixture content changed.'),('authority_ref','changed-authority')):
            changed=deepcopy(self.cases);changed[0][field]=value
            with self.subTest(field=field),self.assertRaises(ProtocolError):
                d.adjudicate(changed,self.votes,self.decisions,self.policy)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases=d.ingest([source(i) for i in range(120)],KEY,allow_test_fixtures=True)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.bundle=self.root/'dataset'
        self.policy=fixture_policy(True)
        votes,decisions=annotated(self.cases,self.policy)
        resolved=d.adjudicate(self.cases,votes,decisions,self.policy)
        self.reservation=d.reserve_splits(self.cases,'fixture-seed',custodian_ref='fixture-custodian',approval_ref='TEST-only')
        self.manifest=d.build_bundle(self.bundle,resolved,self.policy,'fixture-seed',custodian_ref='fixture-custodian',reservation=self.reservation)

    def data(self,split):
        path=self.bundle/('sealed_test/test.jsonl' if split=='test' else f'work/{split}.jsonl')
        return read_jsonl(path)

    def fitted(self):
        cases=self.data('calibration')
        return b.fit_calibrator(self.bundle,predictions(cases,True),run_for(self.manifest,'calibration'))

    def registered(self):
        calibration=self.fitted()
        run=run_for(self.manifest)
        criteria={'max_error':1.0,'min_urgent_recall':0.0,'min_accepted':1,'min_urgent':1,
                  'max_false_alert_rate':1.0,'min_coverage':0.0} # permissive UNIT TEST gates only
        registration=protocol.preregister(self.bundle,predictions(self.data('validation')),run,calibration,[0,0.5,1],criteria,
            approval_ref='TEST-only',custodian_ref='fixture-custodian',allow_test_fixtures=True)
        return calibration,registration

    def test_group_partitions_are_disjoint_and_deterministic(self):
        first=d.split_cases(self.cases,'fixture-seed')
        reversed_parts=d.split_cases(list(reversed(self.cases)),'fixture-seed')
        self.assertEqual({s:{c['case_id'] for c in rows} for s,rows in first.items()},
                         {s:{c['case_id'] for c in rows} for s,rows in reversed_parts.items()})
        ids={s:{c['group_id'] for c in rows} for s,rows in first.items()}
        for a in ids:
            for z in ids:
                if a!=z: self.assertFalse(ids[a]&ids[z])
        same=[dict(self.cases[0]),dict(self.cases[1],group_id=self.cases[0]['group_id'])]
        splits=d.split_cases(same,'fixture-seed')
        self.assertEqual(sum(bool(v) for v in splits.values()),1)

    def test_manifest_detects_data_and_manifest_tampering(self):
        verify_bundle(self.bundle)
        target=self.bundle/'work/development.jsonl'
        target.write_text(target.read_text()+'{}\n')
        with self.assertRaises(ProtocolError): verify_bundle(self.bundle)

    def test_reservation_must_precede_labels_and_bind_sources_and_seed(self):
        resolved=self.data('development')
        with self.assertRaises(ProtocolError):
            d.reserve_splits(resolved,'fixture-seed',custodian_ref='fixture-custodian',approval_ref='TEST-only')
        votes,decisions=annotated(self.cases,self.policy)
        cases=d.adjudicate(self.cases,votes,decisions,self.policy)
        with self.assertRaises(ProtocolError):
            d.build_bundle(self.root/'wrong-seed',cases,self.policy,'changed-seed',custodian_ref='fixture-custodian',reservation=self.reservation)
        cases[0]['text']='Unit fixture changed after reservation.'
        with self.assertRaises(ProtocolError):
            d.build_bundle(self.root/'changed-source',cases,self.policy,'fixture-seed',custodian_ref='fixture-custodian',reservation=self.reservation)

    def test_dev_workflow_never_opens_sealed_inputs_or_labels(self):
        original_bytes,original_text=Path.read_bytes,Path.read_text
        def checked_bytes(path,*args,**kwargs):
            if 'sealed_test' in path.parts: raise AssertionError('Test data was opened')
            return original_bytes(path,*args,**kwargs)
        def checked_text(path,*args,**kwargs):
            if 'sealed_test' in path.parts: raise AssertionError('Test data was opened')
            return original_text(path,*args,**kwargs)
        val=self.data('validation');cal=self.data('calibration')
        with patch.object(Path,'read_bytes',checked_bytes),patch.object(Path,'read_text',checked_text):
            fitted=b.fit_calibrator(self.bundle,predictions(cal,True),run_for(self.manifest,'calibration'))
            b.score_work(self.bundle,'validation',predictions(val),run_for(self.manifest),calibrator=fitted,threshold=0.5)

    def test_dev_commands_reject_test_split_and_calibration_scoring(self):
        for split in ('test','holdout','calibration'):
            with self.assertRaises(ProtocolError): b.score_work(self.bundle,split,[],{})

    def test_route_flags_and_missing_scores_cannot_be_calibrated(self):
        rows=predictions(self.data('calibration'),True)
        for kind in ('route_flag','unavailable'):
            with self.assertRaises(ProtocolError): b.fit_calibrator(self.bundle,rows,run_for(self.manifest,'calibration',score_kind=kind))
        rows[0]['confidence_score']=None
        with self.assertRaises(ProtocolError): b.fit_calibrator(self.bundle,rows,run_for(self.manifest,'calibration'))

    def test_fit_rejects_wrong_partition_or_constant_scores(self):
        with self.assertRaises(ProtocolError): b.fit_calibrator(self.bundle,predictions(self.data('validation')),run_for(self.manifest))
        rows=predictions(self.data('calibration'),True)
        for row in rows: row['confidence_score']=1.0
        with self.assertRaises(ProtocolError): b.fit_calibrator(self.bundle,rows,run_for(self.manifest,'calibration'))

    def test_isotonic_output_is_monotone_and_artifact_bound(self):
        artifact=self.fitted();run=run_for(self.manifest)
        values=[b.calibrated_score(artifact,i/20,run,self.manifest) for i in range(21)]
        self.assertEqual(values,sorted(values))
        with self.assertRaises(ProtocolError): b.calibrated_score(artifact,0.9,dict(run,model_digest='d'*64),self.manifest)
        broken=deepcopy(artifact);broken['blocks'][0][3]+=1
        with self.assertRaises(ProtocolError): b.calibrated_score(broken,0.9,run,self.manifest)

    def test_isotonic_pools_violations_and_aggregates_ties(self):
        cases=self.data('calibration');rows=predictions(cases)
        for i,row in enumerate(rows):
            row['confidence_score']=0.2 if i<len(rows)//2 else 0.8
            if i>=len(rows)//2: row.update(issue_type='Lighting',department='Facilities')
        artifact=b.fit_calibrator(self.bundle,rows,run_for(self.manifest,'calibration'))
        self.assertEqual(len(artifact['blocks']),1)
        low,high,n,total=artifact['blocks'][0]
        self.assertEqual((low,high,n,total),(0.2,0.8,len(rows),len(rows)//2))

    def test_threshold_selection_requires_bounds_and_explicit_support(self):
        cal=self.fitted();rows=predictions(self.data('validation'))
        curve=b.threshold_curve(self.bundle,rows,run_for(self.manifest),cal,[0])
        criteria={'max_error':0.0,'min_urgent_recall':1.0,'min_accepted':1,'min_urgent':1,
                  'max_false_alert_rate':0.0,'min_coverage':0.0}
        self.assertIsNone(b.select_threshold(curve,**criteria)) # perfect point estimates do not suffice
        criteria.update(max_error=1.0,min_urgent_recall=0.0,max_false_alert_rate=1.0,min_accepted=10000)
        self.assertIsNone(b.select_threshold(curve,**criteria))
        criteria['min_accepted']=0
        with self.assertRaises(ProtocolError): b.select_threshold(curve,**criteria)

    def test_unapproved_policy_blocks_registration(self):
        cal=self.fitted();rows=predictions(self.data('validation'))
        with patch.object(protocol,'approved',return_value=False):
            with self.assertRaises(ProtocolError):
                protocol.preregister(self.bundle,rows,run_for(self.manifest),cal,[0],{},
                    approval_ref='TEST-only',custodian_ref='fixture-custodian',allow_test_fixtures=True)

    def test_abstention_retains_urgent_signals(self):
        rows=self.data('validation');pred=predictions(rows)
        for row in pred: row['action']='NEEDS_REVIEW'
        result=b.score_work(self.bundle,'validation',pred,run_for(self.manifest),calibrator=self.fitted(),threshold=1)
        self.assertEqual(result['coverage'],0)
        self.assertIsNone(result['selective_error'])
        self.assertEqual(result['urgent_recall'],1)
        self.assertFalse(result['is_model_evaluation'])

    def test_invalid_results_count_as_failures_and_urgent_misses(self):
        rows=self.data('validation');pred=predictions(rows)
        i=next(i for i,c in enumerate(rows) if c['label']['priority']=='URGENT')
        pred[i].update(status='error',issue_type=None,department=None,priority=None,confidence_score=None,
                       action='NEEDS_REVIEW',safety_signal=False,error_kind='fixture-timeout')
        result=b.score_work(self.bundle,'validation',pred,run_for(self.manifest))
        self.assertEqual(result['error_counts']['missed_urgent'],1)
        self.assertGreater(result['failure_rate'],0)
        self.assertLess(result['urgent_recall'],1)
        self.assertNotIn('text',canonical(result).decode())

    def test_nonfinite_missing_duplicate_or_mismatched_predictions_rejected(self):
        rows=self.data('validation')
        for mutation in ('nan','missing','duplicate','hash'):
            pred=predictions(rows);run=run_for(self.manifest)
            if mutation=='nan': pred[0]['confidence_score']=float('nan')
            elif mutation=='missing': pred.pop()
            elif mutation=='duplicate': pred[-1]=pred[0]
            else: run['partition_sha256']='f'*64
            with self.subTest(mutation=mutation),self.assertRaises(ProtocolError): b.score_work(self.bundle,'validation',pred,run)

    def test_calibration_metrics_are_known_and_no_support_is_not_perfect_recall(self):
        result=b.calibration_metrics([0,1],[0,1])
        self.assertEqual((result['brier'],result['ece']),(0,0))
        self.assertIsNone(b.wilson(0,0))
        rows=[c for c in self.data('validation') if c['label']['priority']!='URGENT']
        result=b.report(rows,predictions(rows),run_for(self.manifest),self.manifest)
        self.assertIsNone(result['urgent_recall'])

    def test_unassessed_priority_is_not_a_false_alert_negative(self):
        rows=deepcopy(self.data('validation')[:1]);rows[0]['label']=label(review=True)
        rows[0]['label']['priority']=None
        pred=predictions(rows);pred[0].update(priority='URGENT',safety_signal=True)
        result=b.report(rows,pred,run_for(self.manifest),self.manifest)
        self.assertEqual(result['unassessed_priority_support'],1)
        self.assertEqual(result['known_nonurgent_support'],0)
        self.assertEqual(result['false_safety_alerts'],0)
        self.assertIsNone(result['false_alert_rate'])
        self.assertIsNone(result['false_alert_rate_ci95'])

    def test_candidates_use_paired_data_and_same_adapter(self):
        pred=predictions(self.data('validation'))
        runs=[{'run':run_for(self.manifest),'predictions':pred},
              {'run':run_for(self.manifest,candidate_id='fixture-qwen14',model_name='fixture:14b'),'predictions':pred}]
        result=b.compare_candidates(self.bundle,runs)
        self.assertEqual(result['paired'][0]['mcnemar_exact_p'],1)
        self.assertFalse(result['production_promotion'])
        runs[1]['run']['adapter_version']='other-adapter'
        with self.assertRaises(ProtocolError): b.compare_candidates(self.bundle,runs)

    def test_final_workflow_rejects_fixture_data_by_default(self):
        cal,registration=self.registered()
        with self.assertRaises(ProtocolError): protocol.release_test_inputs(self.bundle,registration,self.root/'inputs.jsonl')

    def test_one_shot_test_inputs_are_label_free_and_second_score_is_blocked(self):
        cal,registration=self.registered()
        output=self.root/'inputs.jsonl'
        protocol.release_test_inputs(self.bundle,registration,output,allow_test_fixtures=True)
        self.assertTrue(all(set(r)=={'case_id','text'} for r in read_jsonl(output)))
        rows=self.data('test');run=run_for(self.manifest,'test')
        result=protocol.final_score(self.bundle,predictions(rows),run,cal,registration,allow_test_fixtures=True)
        self.assertEqual(result['errors'],[])
        self.assertFalse(result['production_promotion'])
        self.assertFalse(result['is_model_evaluation'])
        with self.assertRaises(ProtocolError): protocol.final_score(self.bundle,predictions(rows),run,cal,registration,allow_test_fixtures=True)

    def test_cannot_reselect_after_independent_input_release(self):
        cal,registration=self.registered()
        protocol.release_test_inputs(self.bundle,registration,self.root/'inputs.jsonl',allow_test_fixtures=True)
        with self.assertRaises(ProtocolError): self.registered()

    def test_changed_registration_or_candidate_cannot_spend_final_attempt(self):
        cal,registration=self.registered()
        changed=deepcopy(registration);changed['threshold']=0.123
        with self.assertRaises(ProtocolError):
            protocol.release_test_inputs(self.bundle,changed,self.root/'invalid-inputs',allow_test_fixtures=True)
        protocol.release_test_inputs(self.bundle,registration,self.root/'inputs',allow_test_fixtures=True)
        with self.assertRaises(ProtocolError):
            protocol.final_score(self.bundle,predictions(self.data('test')),
                run_for(self.manifest,'test',model_digest='d'*64),cal,registration,allow_test_fixtures=True)
        self.assertFalse((self.bundle/'sealed_test/final_attempt.json').exists())

    def test_final_labels_are_not_opened_until_attempt_is_claimed(self):
        cal,registration=self.registered()
        protocol.release_test_inputs(self.bundle,registration,self.root/'inputs',allow_test_fixtures=True)
        rows=predictions(self.data('test'));run=run_for(self.manifest,'test')
        original_bytes,original_text=Path.read_bytes,Path.read_text
        def check(path):
            if path.name=='test.jsonl' and 'sealed_test' in path.parts:
                self.assertTrue((self.bundle/'sealed_test/final_attempt.json').exists())
        def checked_bytes(path,*args,**kwargs):
            check(path)
            return original_bytes(path,*args,**kwargs)
        def checked_text(path,*args,**kwargs):
            check(path)
            return original_text(path,*args,**kwargs)
        with patch.object(Path,'read_bytes',checked_bytes),patch.object(Path,'read_text',checked_text):
            protocol.final_score(self.bundle,rows,run,cal,registration,allow_test_fixtures=True)

    def test_concurrent_final_scoring_consumes_exactly_one_attempt(self):
        cal,registration=self.registered()
        protocol.release_test_inputs(self.bundle,registration,self.root/'inputs',allow_test_fixtures=True)
        rows=predictions(self.data('test'));run=run_for(self.manifest,'test')
        def score():
            try:
                return protocol.final_score(self.bundle,rows,run,cal,registration,allow_test_fixtures=True)['status']
            except ProtocolError:
                return 'BLOCKED'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:score(),range(2)))
        self.assertCountEqual(results,['FINAL_TEST_RESEARCH_REPORT','BLOCKED'])

    def test_crash_after_final_attempt_receipt_spends_test(self):
        cal,registration=self.registered()
        protocol.release_test_inputs(self.bundle,registration,self.root/'inputs.jsonl',allow_test_fixtures=True)
        with patch.object(protocol,'report',side_effect=RuntimeError('unit injected crash')):
            with self.assertRaises(RuntimeError): protocol.final_score(self.bundle,predictions(self.data('test')),run_for(self.manifest,'test'),cal,registration,allow_test_fixtures=True)
        self.assertTrue((self.bundle/'sealed_test/final_attempt.json').exists())
        with self.assertRaises(ProtocolError): protocol.final_score(self.bundle,predictions(self.data('test')),run_for(self.manifest,'test'),cal,registration,allow_test_fixtures=True)

    def test_output_guards_prevent_overwrite_and_source_writes(self):
        target=self.root/'new.json'
        write_json(target,{'fixture':True})
        with self.assertRaises(ProtocolError): write_json(target,{'fixture':False})
        for path in (ROOT/'evaluation/frozen/forbidden.json',ROOT/'backend/forbidden.json',ROOT/'experiments/forbidden.json'):
            with self.assertRaises(ProtocolError): write_json(path,{})


class OfflineAndPlanTests(unittest.TestCase):
    def test_plan_contains_distinct_qwen_sizes_and_no_measured_results(self):
        plan=protocol.experiment_plan()
        self.assertEqual({m['name'] for m in plan['models']},{'deepseek-r1:8b','qwen3:8b','qwen3:14b'})
        self.assertTrue(all(m['weight_digest'] is None and m['execution_status']=='NOT_RUN' for m in plan['models']))
        for prompt in plan['prompts']:
            self.assertEqual(prompt['sha256'],hashlib.sha256(prompt['text'].encode()).hexdigest())
        self.assertIn('Allowed issue types:',plan['prompts'][0]['text'])

    def test_package_has_no_inference_or_network_imports(self):
        forbidden={'urllib','requests','httpx','supabase','anthropic','socket','subprocess','app'}
        for path in Path(protocol.__file__).parent.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
                self.assertFalse(any(name.split('.')[0] in forbidden for name in names),path.name)

    def test_cli_never_prints_private_records_or_overwrites_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'plan.json'
            with patch('sys.stdout',new=io.StringIO()):
                self.assertEqual(main(['plan','--out',str(out)]),0)
            with patch('sys.stderr',new=io.StringIO()) as error:
                self.assertEqual(main(['plan','--out',str(out)]),2)
            self.assertNotIn('fixture-source',error.getvalue())


if __name__=='__main__':
    unittest.main()
