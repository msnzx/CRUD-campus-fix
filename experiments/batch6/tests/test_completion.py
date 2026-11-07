"""Offline Batch 6 transport, model instrumentation, evaluation and monitor faults."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'backend'))
import httpx
from app.observability import tracing,otlp,models,evaluation,health,monitor,registry


class OtlpTests(unittest.TestCase):
    def span(self,exporter):
        with tracing.start_trace('fixture',tracing.SpanKind.LLM,exporter=exporter) as span:
            tracing.record_llm_span(span,model_name='fixture',prompt='private student@example.org',output='secret',prompt_tokens=3,completion_tokens=2)
        return span

    def test_wire_payload_at_mock_collector_is_otlp_json_and_redacted(self):
        requests=[]
        def receive(request):
            requests.append(request);return httpx.Response(200,json={})
        exporter=otlp.LocalOTLPExporter(allow_local=True,transport=httpx.MockTransport(receive))
        span=self.span(exporter);self.assertEqual(requests,[])  # no synchronous network on span finish
        exporter.flush();self.assertEqual(len(requests),1)
        payload=json.loads(requests[0].content)
        wire=payload['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
        self.assertEqual(wire['traceId'],span.trace_id);self.assertEqual(wire['spanId'],span.span_id)
        self.assertEqual(wire['kind'],1);self.assertEqual(wire['status']['code'],1)
        self.assertGreaterEqual(int(wire['endTimeUnixNano']),int(wire['startTimeUnixNano']))
        self.assertEqual(len(wire['startTimeUnixNano']),19)
        self.assertEqual(requests[0].headers['Content-Type'],'application/json')
        self.assertNotIn('student@example.org',requests[0].content.decode());self.assertNotIn('secret',requests[0].content.decode())
        self.assertEqual(exporter.stats()['accepted'],1)

    def test_remote_dns_credentials_redirect_targets_and_default_opt_in_blocked(self):
        for url in ('https://collector.invalid/v1/traces','http://localhost:6006/v1/traces',
                    'http://127.0.0.1.evil/v1/traces','http://u:p@127.0.0.1/v1/traces',
                    'http://127.0.0.1/v1/traces?q=secret','http://169.254.169.254/v1/traces'):
            with self.assertRaises(RuntimeError):otlp.LocalOTLPExporter(url,allow_local=True)
        with self.assertRaises(RuntimeError):otlp.LocalOTLPExporter()

    def test_failure_retry_retains_spans_then_success_drains(self):
        for code in (429,502,503,504):
            replies=[httpx.Response(code,headers={'Location':'https://external.invalid'}),httpx.Response(200,json={})]
            exporter=otlp.LocalOTLPExporter(allow_local=True,transport=httpx.MockTransport(lambda r:replies.pop(0)))
            self.span(exporter);exporter.flush();self.assertEqual(exporter.stats()['pending'],1)
            with patch.object(otlp.time,'monotonic',return_value=exporter._retry_at+1): exporter.flush()
            self.assertEqual(exporter.stats()['pending'],0)
            self.assertEqual(exporter.stats()['failures'],1)

    def test_partial_rejection_is_detected_without_retrying_accepted_subset(self):
        exporter=otlp.LocalOTLPExporter(allow_local=True,transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'partialSuccess':{'rejectedSpans':'1','errorMessage':'PRIVATE'}})))
        self.span(exporter);exporter.flush()
        self.assertEqual(exporter.stats()['pending'],0);self.assertEqual(exporter.stats()['rejected'],1)
        self.assertNotIn('PRIVATE',str(exporter.stats()))

    def test_non_retryable_status_bad_json_and_oversized_response_are_visible_drops(self):
        for response in (httpx.Response(400),httpx.Response(302,headers={'Location':'http://external.invalid'}),
                         httpx.Response(200,content=b'not json'),httpx.Response(200,content=b'x'*20000)):
            exporter=otlp.LocalOTLPExporter(allow_local=True,transport=httpx.MockTransport(lambda r:response))
            self.span(exporter);exporter.flush()
            self.assertEqual(exporter.stats()['pending'],0)
            self.assertEqual(exporter.stats()['dropped'],1)
            self.assertEqual(exporter.stats()['last_error'],'PERMANENT_EXPORT_FAILURE')

    def test_retry_after_prevents_repeated_sends_before_deadline(self):
        receive=MagicMock(return_value=httpx.Response(429,headers={'Retry-After':'60'}))
        exporter=otlp.LocalOTLPExporter(allow_local=True,transport=httpx.MockTransport(receive))
        self.span(exporter);exporter.flush();exporter.flush()
        self.assertEqual(receive.call_count,1);self.assertEqual(exporter.stats()['pending'],1)

    def test_bounded_queue_and_concurrent_flush(self):
        exporter=otlp.LocalOTLPExporter(allow_local=True,capacity=4,transport=httpx.MockTransport(lambda r:httpx.Response(200,json={})))
        for _ in range(12):self.span(exporter)
        self.assertEqual(exporter.stats()['dropped'],8)
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:exporter.flush(),range(8)))
        self.assertEqual(exporter.stats()['accepted'],4)


class ModelInstrumentationTests(unittest.TestCase):
    def setUp(self):
        tracing.GLOBAL_TRACE_EXPORTER.clear();registry.GLOBAL_COST_TRACKER.reset()
        self.cfg=SimpleNamespace(observability_enabled=True,ollama_model='deepseek-r1:8b')

    def test_exact_prompt_digest_and_actual_usage_without_content(self):
        response={'model':'deepseek-r1:8b','message':{'content':'private report'},'prompt_eval_count':8,'eval_count':3}
        with tracing.start_trace('parent') as parent:
            result=models.observed_model_call(self.cfg,'fixture_model','STATIC INSTRUCTIONS',lambda:response)
        self.assertIs(result,response)
        span=tracing.GLOBAL_TRACE_EXPORTER.get_spans()[0]
        self.assertEqual(span['parent_id'],parent.span_id)
        self.assertEqual(span['attributes']['pipeline.version'],'sha256:'+hashlib.sha256(b'STATIC INSTRUCTIONS').hexdigest())
        self.assertEqual(span['attributes']['llm.token_count.total'],11)
        self.assertNotIn('private report',json.dumps(span))
        self.assertEqual(registry.GLOBAL_COST_TRACKER.get_summary()['total_tokens'],11)

    def test_unknown_usage_is_omitted_and_exception_is_preserved(self):
        models.observed_model_call(self.cfg,'fixture_unknown','STATIC',lambda:{'message':{'content':'private'}})
        self.assertNotIn('llm.token_count.total',tracing.GLOBAL_TRACE_EXPORTER.get_spans()[0]['attributes'])
        self.assertEqual(registry.GLOBAL_COST_TRACKER.get_summary()['total_requests'],0)
        error=TimeoutError('PRIVATE PROVIDER ERROR')
        with self.assertRaises(TimeoutError) as caught:
            models.observed_model_call(self.cfg,'fixture_timeout','STATIC',MagicMock(side_effect=error))
        self.assertIs(caught.exception,error)
        self.assertNotIn('PRIVATE PROVIDER ERROR',json.dumps(tracing.GLOBAL_TRACE_EXPORTER.get_spans()))

    def test_disabled_or_broken_telemetry_never_alters_model_result(self):
        response={'ok':True};call=MagicMock(return_value=response)
        self.cfg.observability_enabled=False
        self.assertIs(models.observed_model_call(self.cfg,'fixture','STATIC',call),response)
        self.assertEqual(tracing.GLOBAL_TRACE_EXPORTER.get_spans(),[])
        self.cfg.observability_enabled=True
        with patch.object(models.GLOBAL_PROMPT_REGISTRY,'register',side_effect=RuntimeError()):
            self.assertIs(models.observed_model_call(self.cfg,'fixture','STATIC',call),response)
        self.assertEqual(call.call_count,2)

    def test_existing_classifier_and_enrichment_calls_are_instrumented_without_prompt_changes(self):
        import io
        from app import classify,enrich
        self.cfg.ollama_url='http://127.0.0.1:11434/api/chat';self.cfg.ollama_timeout_seconds=3
        private='PRIVATE STUDENT REPORT'
        replies=[{'issue_type':'Other','priority':'URGENT'},
                 {'title':'Fixture title','description':'Fixture description','explanation':'Fixture explanation'}]
        for module,function,args,response in ((classify,classify._classify_with_deepseek,(private,['Other'],self.cfg),replies[0]),
                                              (enrich,enrich._generate,(private,'Other','URGENT',self.cfg),replies[1])):
            data={'message':{'content':json.dumps(response)},'prompt_eval_count':7,'eval_count':4}
            with patch.object(module.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(data).encode())) as send:
                function(*args)
            payload=json.loads(send.call_args.args[0].data)
            self.assertIn(private,payload['messages'][1]['content'])
        spans=tracing.GLOBAL_TRACE_EXPORTER.get_spans()
        self.assertEqual({s['name'] for s in spans},{'classification_model','enrichment_model'})
        self.assertNotIn(private,json.dumps(spans))
        self.assertEqual(registry.GLOBAL_COST_TRACKER.get_summary()['total_tokens'],22)

    def test_retrieval_and_mock_vision_spans_never_capture_arguments_or_images(self):
        from app.knowledge.tools import KnowledgeTools
        from app.vision.worker import MockClaudeProvider,_provider_response
        client=MagicMock();client.rpc.return_value.execute.return_value.data=[]
        with tracing.start_trace('request'):
            result=KnowledgeTools(client,observability_enabled=True).call('departments',{'query':'PRIVATE QUERY'})
            provider=MockClaudeProvider(lambda payload:{'fixture':'answer'})
            _provider_response(provider,{'image':'PRIVATE BASE64'},True)
        spans=tracing.GLOBAL_TRACE_EXPORTER.get_spans()
        self.assertEqual(result['status'],'ABSTAIN')
        self.assertEqual({s['attributes']['openinference.span.kind'] for s in spans},{'CHAIN','LLM','RETRIEVER','TOOL'})
        self.assertNotIn('PRIVATE',json.dumps(spans))
        self.assertTrue(all(s['parent_id'] for s in spans[:-1]))

    def test_tool_trace_setup_failure_preserves_authorization_and_result(self):
        from app.knowledge.tools import KnowledgeTools
        client=MagicMock();client.rpc.return_value.execute.return_value.data=[]
        with patch.object(tracing,'start_trace',side_effect=RuntimeError('telemetry broken')):
            result=KnowledgeTools(client,observability_enabled=True).call('departments',{'query':'fixture'})
        self.assertEqual(result['status'],'ABSTAIN');self.assertEqual(client.rpc.call_count,1)


class EvaluationCompletionTests(unittest.TestCase):
    def test_measured_exact_match_and_ragas_field_mapping(self):
        result=evaluation.run_ragas_evaluation(['Q1','Q2'],['yes','YES'],[['evidence'],[]],['yes','yes'],offline_metrics=('exact_match',))
        self.assertEqual(result['metrics'],{'exact_match':.5});self.assertEqual(result['model_calls'],0)
        self.assertIn('faithfulness',result['not_evaluated'])
        rows=evaluation.ragas_samples(['Q'],['A'],[['C']],['R'])
        self.assertEqual(set(rows[0]),{'user_input','response','retrieved_contexts','reference'})
        self.assertNotIn('Q1',json.dumps(result))

    def test_relevance_requires_explicit_unique_ids_and_unknown_is_not_zero(self):
        result=evaluation.evaluate_retrieval_ids(['a','b'],['b','c'])
        self.assertEqual(result['id_precision'],.5);self.assertEqual(result['id_recall'],.5)
        self.assertIsNone(evaluation.evaluate_retrieval_ids([],[])['id_recall'])
        with self.assertRaises(ValueError):evaluation.evaluate_retrieval_ids(['a','a'],['a'])

    def test_invalid_inputs_or_unconfigured_semantic_metrics_fail_closed(self):
        for contexts in ([['c']*21],[[object()]]):
            with self.assertRaises(ValueError):evaluation.run_ragas_evaluation(['Q'],['A'],contexts,['R'],offline_metrics=('exact_match',))
        with self.assertRaises(ValueError):evaluation.run_ragas_evaluation(['Q'],['A'],[[]],['R'],offline_metrics=('faithfulness',))


class CredentialScanTests(unittest.TestCase):
    def test_encoded_service_role_and_secret_patterns_are_detected_without_echoing(self):
        import base64
        from experiments.batch6.security_scan import findings
        payload=base64.urlsafe_b64encode(json.dumps({'role':'service_role'}).encode()).rstrip(b'=')
        data=b'eyJfixture.'+payload+b'.'+b'x'*32
        self.assertEqual(findings(data),['service_role_jwt'])
        self.assertEqual(findings(b'sb_secret_'+b'x'*30),['supabase_secret'])
        self.assertEqual(findings(b'sk-ant-'+b'x'*30),['anthropic_secret'])
        self.assertEqual(findings(b'{"role":"service_role"}'),[])  # role declarations are not credentials


class MonitorCompletionTests(unittest.TestCase):
    def setUp(self):health.reset();monitor.reset()

    def admin(self):
        admin=MagicMock()
        admin.rpc.return_value.execute.return_value.data={'observed_at':'FIXTURE','classification':{'due':3},'email':{'due':2},'safety':{'due':1},'workers':[],'workers_truncated':False}
        return admin

    def test_unknown_stale_failed_and_recovered_observations(self):
        self.assertEqual(monitor.database_health()['status'],'UNKNOWN')
        admin=self.admin();monitor.sample_once(admin,'00000000-0000-0000-0000-000000000001')
        self.assertEqual(monitor.database_health()['status'],'FRESH')
        with patch.object(monitor.time,'monotonic',return_value=monitor._sample_clock+61):
            self.assertEqual(monitor.database_health()['status'],'STALE')
        admin.rpc.return_value.execute.side_effect=TimeoutError('private')
        with self.assertRaises(TimeoutError):monitor.sample_once(admin,'fixture')
        self.assertEqual(monitor.database_health()['status'],'UNAVAILABLE')

        self.assertEqual(monitor.database_health()['last_observation']['email']['due'],2)
        admin.rpc.return_value.execute.side_effect=None;monitor.sample_once(admin,'fixture')
        self.assertEqual(monitor.database_health()['status'],'FRESH')

    def test_sampler_never_invents_worker_progress(self):
        health.configure_worker('classification');admin=self.admin()
        monitor.sample_once(admin,'fixture')
        row=admin.table.return_value.upsert.call_args.args[0][0]
        self.assertIsNone(row['progress_at']);self.assertEqual(row['state'],'UNKNOWN')
        health.record_outcome('classification',False);monitor.sample_once(admin,'fixture')
        row=admin.table.return_value.upsert.call_args.args[0][0]
        self.assertEqual(row['state'],'DEGRADED');self.assertIsNotNone(row['progress_at'])

    def test_malformed_rpc_is_not_healthy(self):
        admin=self.admin();admin.rpc.return_value.execute.return_value.data={}
        with self.assertRaises(ValueError):monitor.sample_once(admin,'fixture')
        self.assertEqual(monitor.database_health()['status'],'UNAVAILABLE')


class MonitorLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_failures_are_isolated_and_calls_run_off_event_loop(self):
        import threading
        cfg=SimpleNamespace(supabase_url='http://127.0.0.1:54321',supabase_service_role_key='fixture',observability_interval_seconds=5)
        main_thread=threading.get_ident();calls=[]
        def sample(*args):
            calls.append(threading.get_ident())
            if len(calls)==1:raise TimeoutError('PRIVATE DATABASE ERROR')
        sleeps=0
        async def sleep(_):
            nonlocal sleeps
            sleeps+=1
            if sleeps==2:raise asyncio.CancelledError
        with patch('supabase.create_client'),patch.object(monitor,'sample_once',side_effect=sample), \
             patch.object(otlp,'flush_configured'),patch.object(monitor.asyncio,'sleep',side_effect=sleep):
            with self.assertRaises(asyncio.CancelledError):await monitor.monitor_loop(cfg)
        self.assertEqual(len(calls),2);self.assertTrue(all(identity!=main_thread for identity in calls))
