"""Metadata-only observability security/concurrency/fault regressions. No network."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,MagicMock,patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'backend/tests'))
from test_routing import Database,config
from fastapi.testclient import TestClient
from app import main,notify,classify,enrich
from app.auth import Caller,current_caller
from app.config import Settings,get_settings
from app.observability import health,tracing,registry,redaction,evaluation,runtime
from experiments.batch6.dependencies import audit_environment


class ApiAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.previous=dict(main.app.dependency_overrides)
        self.addCleanup(lambda:(main.app.dependency_overrides.clear(),main.app.dependency_overrides.update(self.previous)))
        main.app.dependency_overrides.clear()
        self.cfg=config(observability_enabled=True)
        main.app.dependency_overrides[get_settings]=lambda:self.cfg
        self.api=TestClient(main.app)

    def test_disabled_configuration_is_not_an_import_time_environment_toggle(self):
        self.cfg=config(observability_enabled=False)
        with patch.dict('os.environ',{'OBSERVABILITY_ENABLED':'true'}),patch('app.auth.create_client') as client:
            for path in ('health','traces','models'):
                self.assertEqual(self.api.get('/api/observability/'+path).status_code,503)
        client.assert_not_called()

    def test_missing_and_invalid_bearer_tokens_never_reach_diagnostics(self):
        for path in ('health','traces','models'):
            self.assertEqual(self.api.get('/api/observability/'+path).status_code,401)
            anon=MagicMock();anon.auth.get_user.side_effect=ValueError('not a user session')
            with patch('app.auth.create_client',return_value=anon):
                response=self.api.get('/api/observability/'+path,headers={'Authorization':'Bearer service-role-looking-secret'})
            self.assertEqual(response.status_code,401)

    def test_missing_admin_auth_configuration_fails_closed_without_client_creation(self):
        self.cfg=config(observability_enabled=True,supabase_service_role_key='')
        with patch('app.auth.create_client') as client:
            self.assertEqual(self.api.get('/api/observability/health',headers={'Authorization':'Bearer fixture'}).status_code,503)
        client.assert_not_called()

    def test_real_auth_dependency_checks_active_profile_and_authoritative_role_each_time(self):
        db=Database();self.addCleanup(db.http.close)
        db.rows['user_departments']=[]
        for role,active,expected in (('STUDENT',True,403),('DEPARTMENT_ADMIN',True,403),
                                    ('SYSTEM_ADMIN',False,403),('SYSTEM_ADMIN',True,200),('STUDENT',True,403)):
            db.rows['users']=[{'user_id':'fixture-user','email':'private-fixture@campus.invalid','active':active,'roles':{'name':role}}]
            for path in ('health','traces','models'):
                anon=MagicMock();anon.auth.get_user.return_value.user.id='fixture-user'
                with patch('app.auth.create_client',side_effect=[anon,db.client]):
                    response=self.api.get('/api/observability/'+path+'?role=SYSTEM_ADMIN&api_key=spoof',
                                          headers={'Authorization':'Bearer fixture-user-jwt'})
                self.assertEqual(response.status_code,expected)
                anon.auth.get_user.assert_called_once_with('fixture-user-jwt')
                self.assertNotIn('private-fixture',response.text)
                self.assertNotIn(self.cfg.supabase_service_role_key,response.text)

    def test_diagnostics_are_read_only_and_public_health_remains_compatible(self):
        main.app.dependency_overrides[current_caller]=lambda:Caller('fixture','fixture@invalid','SYSTEM_ADMIN',[])
        self.assertEqual(self.api.post('/api/observability/traces',json={}).status_code,405)
        self.assertEqual(self.api.get('/api/observability/health').json()['scope'],'PROCESS_LOCAL')
        self.assertEqual(self.api.get('/api/health').status_code,200)


class HealthTests(unittest.TestCase):
    def setUp(self): health.reset();self.addCleanup(health.reset)

    def test_import_has_no_filesystem_side_effect(self):
        with patch.object(Path,'mkdir',side_effect=AssertionError('Import must not create directories')):
            importlib.reload(health)

    def test_unknown_disabled_alive_failed_and_stale_are_distinct(self):
        clock=SimpleNamespace(time=lambda:1000,monotonic=lambda:100)
        with patch.object(health,'time',clock):
            health.configure_worker('classification',stale_after_seconds=10)
            health.configure_worker('vision',enabled=False)
            self.assertEqual(health.snapshot()['workers']['classification']['state'],'UNKNOWN')
            self.assertEqual(health.snapshot()['workers']['vision']['state'],'DISABLED')
            health.record_heartbeat('classification')
            self.assertEqual(health.snapshot()['workers']['classification']['state'],'ALIVE')
            health.record_outcome('classification',False)
            self.assertEqual(health.snapshot()['workers']['classification']['state'],'DEGRADED')
            health.record_outcome('classification',True)
            self.assertEqual(health.snapshot()['workers']['classification']['state'],'HEALTHY')
            clock.monotonic=lambda:111;clock.time=lambda:500
            self.assertEqual(health.snapshot()['workers']['classification']['state'],'STALE')

    def test_backlog_is_unknown_until_observed_and_types_are_strict(self):
        self.assertIsNone(health.get_backlog('classification'))
        self.assertEqual(health.snapshot()['backlog_status'],'UNKNOWN')
        for bad in (True,-1,1.5):
            with self.assertRaises(ValueError): health.set_backlog('classification',bad)
        health.set_backlog('classification',0)
        self.assertEqual(health.get_backlog('classification'),0)
        self.assertEqual(health.snapshot()['backlog_status'],'OBSERVED')

    def test_snapshots_are_copies_and_state_is_bounded(self):
        health.record_outcome('worker',True)
        value=health.snapshot();value['workers']['worker']['last_outcome']['success']=False
        self.assertTrue(health.snapshot()['workers']['worker']['last_outcome']['success'])
        for i in range(63): health.configure_worker('worker'+str(i))
        with self.assertRaises(ValueError): health.configure_worker('overflow')

    def test_concurrent_exports_use_unique_atomic_temp_files(self):
        with tempfile.TemporaryDirectory() as temp:
            def export(i):
                health.record_heartbeat('worker')
                return health.export_snapshot(temp)
            with ThreadPoolExecutor(8) as pool: paths=list(pool.map(export,range(16)))
            payload=json.loads(paths[-1].read_text())
            self.assertEqual(payload['scope'],'PROCESS_LOCAL')
            self.assertEqual(list(paths[-1].parent.glob('*.tmp')),[])

    def test_export_cannot_overwrite_source_directories(self):
        with self.assertRaises(ValueError): health.export_snapshot(ROOT/'backend')


class TraceTests(unittest.TestCase):
    def setUp(self): self.exporter=tracing.InMemoryTraceExporter()

    def test_nested_context_restores_parent_for_later_siblings_and_exceptions(self):
        with tracing.start_trace('root',exporter=self.exporter) as root:
            with tracing.start_trace('child',exporter=self.exporter) as child:
                self.assertEqual(child.parent_id,root.span_id)
                self.assertEqual(child.trace_id,root.trace_id)
            with tracing.start_trace('sibling',exporter=self.exporter) as sibling:
                self.assertEqual(sibling.parent_id,root.span_id)
            with self.assertRaises(ValueError):
                with tracing.start_trace('child',exporter=self.exporter): raise ValueError('PRIVATE REPORT')
            self.assertIs(tracing._ACTIVE_SPAN.get(),root)
        self.assertIsNone(tracing._ACTIVE_SPAN.get())
        self.assertNotIn('PRIVATE REPORT',json.dumps(self.exporter.get_spans()))

    def test_prompts_outputs_tools_events_and_unknown_attributes_are_not_captured(self):
        secret='Alice Jones private dorm report with opaque credentials'
        with tracing.start_trace('llm',exporter=self.exporter) as span:
            tracing.record_llm_span(span,model_name='fixture-model',prompt=secret,output=secret,prompt_tokens=3,completion_tokens=4)
            tracing.record_retrieval_span(span,query=secret,documents=[{'content':secret,'source_uri':secret}])
            tracing.record_tool_span(span,tool_name='campus_policies',parameters={'q':secret},result=secret)
            span.add_event('event',{'body':secret})
            span.set_attribute(secret,secret)
            span.attributes['unexpected']=secret
        payload=self.exporter.get_spans()[0]
        self.assertNotIn(secret,json.dumps(payload))
        self.assertEqual(payload['attributes']['llm.token_count.total'],7)
        self.assertEqual(payload['attributes']['retrieval.document_count'],1)
        self.assertEqual(payload['attributes']['input.value'],'[REDACTED]')

    def test_mutated_attributes_and_getter_results_cannot_change_stored_spans(self):
        span=tracing.start_trace('root',exporter=self.exporter)
        span.set_attribute('llm.token_count.prompt',2);span.finish()
        span.attributes['llm.token_count.prompt']=99
        value=self.exporter.get_spans();value[0]['attributes']['llm.token_count.prompt']=88
        self.assertEqual(self.exporter.get_spans()[0]['attributes']['llm.token_count.prompt'],2)

    def test_concurrent_finish_is_idempotent_and_buffer_evicts_oldest(self):
        exporter=tracing.InMemoryTraceExporter(max_spans=2)
        span=tracing.start_trace('root',exporter=exporter)
        with ThreadPoolExecutor(8) as pool: list(pool.map(lambda _:span.finish(),range(32)))
        self.assertEqual(len(exporter.get_spans()),1)
        tracing.start_trace('child',exporter=exporter).finish()
        tracing.start_trace('sibling',exporter=exporter).finish()
        self.assertEqual([r['name'] for r in exporter.get_spans()],['child','sibling'])
        for capacity in (0,-1,True,2001):
            with self.assertRaises(ValueError): tracing.InMemoryTraceExporter(capacity)

    def test_exporter_failure_does_not_mask_result_or_original_application_error(self):
        exporter=MagicMock();exporter.export.side_effect=RuntimeError('collector unavailable')
        with tracing.start_trace('root',exporter=exporter): result='done'
        self.assertEqual(result,'done');self.assertIsNone(tracing._ACTIVE_SPAN.get())
        with self.assertRaisesRegex(ValueError,'original application failure'):
            with tracing.start_trace('root',exporter=exporter): raise ValueError('original application failure')
        self.assertIsNone(tracing._ACTIVE_SPAN.get())

    def test_remote_export_fails_closed_before_http_calls(self):
        with patch('httpx.post') as request:
            with self.assertRaises(RuntimeError): tracing.PhoenixHTTPExporter('https://unapproved.invalid')
        request.assert_not_called()

    def test_invalid_provider_usage_is_omitted_without_fabricating_or_breaking_work(self):
        with tracing.start_trace('llm',exporter=self.exporter) as span:
            tracing.record_llm_span(span,model_name='fixture',prompt='private',output='private',
                                    prompt_tokens=None,completion_tokens='unknown')
            tracing.record_retrieval_span(span,query='private',documents=None)
        values=self.exporter.get_spans()[0]['attributes']
        self.assertNotIn('llm.token_count.total',values)
        self.assertNotIn('retrieval.document_count',values)


class AsyncTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sibling_tasks_and_to_thread_propagate_without_losing_parent(self):
        exporter=tracing.InMemoryTraceExporter()
        with tracing.start_trace('root',exporter=exporter) as parent:
            async def child():
                with tracing.start_trace('child',exporter=exporter) as span:
                    await asyncio.sleep(0)
                    self.assertEqual(tracing._ACTIVE_SPAN.get(),span)
                    def nested():
                        with tracing.start_trace('sibling',exporter=exporter) as nested:
                            self.assertEqual(nested.parent_id,span.span_id)
                    await asyncio.to_thread(nested)
            await asyncio.gather(child(),child())
            self.assertIs(tracing._ACTIVE_SPAN.get(),parent)
        self.assertIsNone(tracing._ACTIVE_SPAN.get())


class RedactionAndRegistryTests(unittest.TestCase):
    def test_keyed_secrets_cycles_unsupported_values_and_nonfinite_values_are_safe(self):
        payload={'Authorization':'opaque','access_token':'opaque','email':'fixture-person@example.invalid'}
        payload['cycle']=payload;payload['object']=object();payload['nan']=float('nan')
        cleaned=redaction.redact_data(payload)
        self.assertNotIn('opaque',json.dumps(cleaned))
        self.assertNotIn('fixture-person',json.dumps(cleaned))
        self.assertEqual(cleaned['cycle'],'[CYCLE]');self.assertEqual(cleaned['object'],'[UNSUPPORTED]')
        self.assertIsNone(cleaned['nan'])

    def test_existing_redaction_marker_does_not_hide_another_leaked_jwt(self):
        jwt='eyJ'+'a'*12+'.'+'b'*16+'.'+'c'*16
        with self.assertRaises(AssertionError): redaction.assert_zero_sensitive_leaks('[JWT_REDACTED] '+jwt)

    def test_prompt_registry_has_no_fabricated_defaults_and_versions_are_immutable(self):
        store=registry.PromptRegistry();self.assertEqual(store.list_prompts(),[])
        prompt=registry.PromptVersion('fixture','v1','Exact fixture template {value}','Fixture only')
        store.register(prompt);store.register(prompt)
        self.assertEqual(store.get('fixture','v1').template_sha256,hashlib.sha256(prompt.template.encode()).hexdigest())
        self.assertEqual(prompt.render(value='x'),'Exact fixture template x')
        with self.assertRaises(ValueError): store.register(registry.PromptVersion('fixture','v1','Changed','Fixture'))

    def test_local_api_cost_zero_unknown_cost_is_unknown_not_invented(self):
        tracker=registry.CostTracker()
        self.assertEqual(tracker.calculate_cost('deepseek-r1:8b',100,200),0)
        self.assertIsNone(tracker.record_usage('unpriced-model',1,2)['cost_usd'])
        summary=tracker.get_summary()
        self.assertIsNone(summary['total_cost_usd']);self.assertEqual(summary['unknown_cost_requests'],1)
        self.assertIsNone(summary['by_model']['unpriced-model']['total_cost_usd'])
        summary['by_model']['unpriced-model']['requests']=999
        self.assertEqual(tracker.get_summary()['by_model']['unpriced-model']['requests'],1)
        for bad in (-1,True,1.5,float('nan')):
            with self.assertRaises(ValueError): tracker.record_usage('fixture-hash-64',bad,0)

    def test_concurrent_usage_counters_are_not_lost(self):
        tracker=registry.CostTracker()
        with ThreadPoolExecutor(8) as pool: list(pool.map(lambda _:tracker.record_usage('fixture-hash-64',1,2),range(100)))
        self.assertEqual(tracker.get_summary()['total_tokens'],300)
        self.assertEqual(tracker.get_summary()['total_requests'],100)

    def test_ragas_never_constructs_default_providers_or_fabricates_scores(self):
        with patch.object(evaluation,'_require_ragas',side_effect=AssertionError('must not import providers')) as provider:
            with self.assertRaises(RuntimeError): evaluation.run_ragas_evaluation(['fixture'],['fixture'],[['fixture']],['fixture'])
        provider.assert_not_called()
        with self.assertRaises(ValueError): evaluation.run_ragas_evaluation([],[],[],[])

    def test_required_runtime_dependencies_match_lock_without_optional_sdk_imports(self):
        result=audit_environment()
        self.assertEqual(result['issues'],[])
        self.assertEqual(result['runtime_pins'],42)
        self.assertEqual(result['network_calls'],0)


class RuntimeHookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self): health.reset();tracing.GLOBAL_TRACE_EXPORTER.clear();self.addCleanup(health.reset)

    def test_disabled_hooks_do_not_change_health_or_collect_spans(self):
        cfg=config(observability_enabled=False)
        runtime.configure(cfg);runtime.pulse(cfg,'classification');runtime.outcome(cfg,'classification',True)
        self.assertEqual(runtime.observed_call(cfg,'classification',lambda:42),42)
        self.assertEqual(health.snapshot()['workers'],{})
        self.assertEqual(tracing.GLOBAL_TRACE_EXPORTER.get_spans(),[])

    def test_health_failures_are_isolated_and_disabled_workers_are_explicit(self):
        cfg=config(observability_enabled=True,supabase_service_role_key='')
        runtime.configure(cfg)
        self.assertEqual(health.snapshot()['workers']['classification']['state'],'DISABLED')
        with patch.object(health,'record_heartbeat',side_effect=RuntimeError('bad observer')):
            runtime.pulse(cfg,'classification')
        self.assertEqual(runtime.observed_call(cfg,'classification',lambda:42),42)

    def test_new_lifespan_starts_unknown_instead_of_inheriting_old_success(self):
        cfg=config(observability_enabled=True)
        health.record_outcome('classification',True)
        runtime.configure(cfg)
        self.assertEqual(health.snapshot()['workers']['classification']['state'],'UNKNOWN')

    def test_trace_metadata_failure_cannot_replace_a_successful_application_result(self):
        span=MagicMock();span.set_attribute.side_effect=RuntimeError('injected observer failure')
        with patch.object(tracing,'start_trace',return_value=span):
            self.assertEqual(runtime.observed_call(config(observability_enabled=True),'classification',lambda:42),42)

    async def test_classification_failure_records_degraded_progress_and_keeps_processing(self):
        cfg=config(observability_enabled=True);admin=MagicMock()
        claims=[{'claimed':True,'claim_token':'fixture','ticket':{'ticket_id':1}},
                {'claimed':True,'claim_token':'fixture','ticket':{'ticket_id':2}},{'claimed':False,'reason':'empty'}]
        calls=[]
        def process(claim,*args):
            calls.append(claim['ticket']['ticket_id'])
            if len(calls)==1: raise ValueError('PRIVATE FIXTURE EXCEPTION')
            return main.ClassifyResult(ticket_id=2,routing_status='AUTO_ROUTED',detail='fixture')
        with patch.object(main,'create_client',return_value=admin),patch.object(main.ai_jobs,'claim_next',side_effect=claims), \
             patch.object(main,'_run_claimed_ticket',side_effect=process), \
             patch.object(main.asyncio,'sleep',new=AsyncMock(side_effect=[None,asyncio.CancelledError()])),self.assertLogs(main.log,level='ERROR') as logged:
            with self.assertRaises(asyncio.CancelledError): await main.classification_loop(cfg)
        self.assertEqual(calls,[1,2])
        self.assertEqual(health.snapshot()['workers']['classification']['state'],'DEGRADED')
        self.assertEqual(len(tracing.GLOBAL_TRACE_EXPORTER.get_spans()),2)
        self.assertNotIn('PRIVATE',json.dumps(tracing.GLOBAL_TRACE_EXPORTER.get_spans()))
        self.assertNotIn('PRIVATE',str(logged.output))

    def test_model_and_enrichment_logs_do_not_echo_provider_content(self):
        db=Database();self.addCleanup(db.http.close);classify._REF_CACHE=None
        with patch.object(classify,'_classify_with_deepseek',side_effect=ValueError('PRIVATE PROVIDER BODY')), \
             self.assertLogs(classify.log,level='WARNING') as logs:
            self.assertIsNone(classify.classify(db.ticket,db.client,config()))
        self.assertNotIn('PRIVATE',str(logs.output))
        with patch.object(enrich,'_generate',side_effect=ValueError('PRIVATE PROVIDER BODY')), \
             self.assertLogs(enrich.log,level='WARNING') as logs:
            self.assertFalse(enrich.enrich_ticket(db.ticket,db.client,issue_type_id=11,priority_id=2,
                settings=config(),analysis_token='fixture',expected_revision=1))
        self.assertNotIn('PRIVATE',str(logs.output))

    async def test_safety_retry_and_notification_loops_report_failures(self):
        cfg=config(observability_enabled=True);admin=MagicMock()
        admin.rpc.return_value.execute.side_effect=ConnectionError('fixture outage')
        with patch.object(main,'create_client',return_value=admin), \
             patch.object(main.asyncio,'sleep',new=AsyncMock(side_effect=[None,asyncio.CancelledError()])):
            with self.assertRaises(asyncio.CancelledError): await main.safety_retry_loop(cfg)
        self.assertEqual(health.snapshot()['workers']['safety_retry']['state'],'DEGRADED')
        with patch.object(notify,'create_client',return_value=MagicMock()), \
             patch.object(notify,'dispatch_pending',return_value={'sent':0,'failed':1,'skipped':0}), \
             patch.object(notify.asyncio,'sleep',new=AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError): await notify.dispatch_loop(cfg)
        self.assertEqual(health.snapshot()['workers']['notifications']['state'],'DEGRADED')

    async def test_cancelled_classification_claim_does_not_report_success(self):
        cfg=config(observability_enabled=True)
        with patch.object(main,'create_client',return_value=MagicMock()), \
             patch.object(main.asyncio,'sleep',new=AsyncMock(return_value=None)), \
             patch.object(main.asyncio,'to_thread',new=AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError): await main.classification_loop(cfg)
        self.assertFalse(health.snapshot()['workers']['classification']['last_outcome']['success'])


if __name__=='__main__': unittest.main()
