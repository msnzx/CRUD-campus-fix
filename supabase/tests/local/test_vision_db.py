"""Batch 3 optional SQL and production worker against disposable local PG."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
import harness
import test_ai_orchestration_db as fixtures


class SqlVisionAdmin(fixtures._SqlAdmin):
    def rpc(self,name,params):
        signatures={
            'vision_shadow_discover':{'p_after':'bigint','p_limit':'integer'},
            'vision_shadow_claim':{'p_worker':'text','p_lease_seconds':'integer','p_provenance':'jsonb'},
            'vision_shadow_finish':{'p_ticket':'bigint','p_token':'uuid','p_findings':'jsonb',
                                    'p_provenance':'jsonb','p_error':'text','p_retry':'boolean'}}
        admin=self
        class Call:
            def execute(self):
                from psycopg.types.json import Jsonb
                types=signatures[name];keys=list(params)
                args=','.join(f'{key}=>%s::{types[key]}' for key in keys)
                data=admin._run(f'select public.{name}({args}) as r',
                    [Jsonb(params[key]) if types[key]=='jsonb' else params[key] for key in keys])
                return fixtures._Result(data[0]['r'])
        return Call()


@unittest.skipUnless(harness.available(),'pgserver/psycopg not installed')
class VisionDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('SUPABASE_URL','http://localhost')
        os.environ.setdefault('SUPABASE_PUBLISHABLE_KEY','test')
        fixtures.AiOrchestrationDbTests.setUpClass.__func__(cls)
        with harness.connect(cls.uri) as conn:
            conn.execute((harness.REPO/'supabase/optional/batch3_vision_shadow.sql').read_text())
        sys.path.insert(0,str(harness.REPO/'backend'))
        sys.path.insert(0,str(harness.REPO/'backend/tests'))
        from app.vision import worker,contract,attachments
        import test_vision
        cls.worker,cls.contract,cls.attachments,cls.fx=worker,contract,attachments,test_vision

    conn=fixtures.AiOrchestrationDbTests.conn
    svc=fixtures.AiOrchestrationDbTests.svc
    fresh=fixtures.AiOrchestrationDbTests.fresh
    new_ticket=fixtures.AiOrchestrationDbTests.new_ticket
    ticket=fixtures.AiOrchestrationDbTests.ticket
    def notifications(self,t):
        return self.c.execute('select * from public.notifications where ticket_id=%s order by notification_id',(t,)).fetchall()

    def setUp(self):
        self.c=self.svc()
        self.c.execute('delete from public.ticket_vision_runs')
        self.c.execute('delete from public.ticket_vision_jobs')
        self.c.execute('delete from public.attachments')
        with harness.connect(self.uri) as owner:
            owner.execute('delete from storage.objects')

    def attach(self,t,*,path=None):
        path=path or f'{t}/{uuid.uuid4()}.png'
        owner=self.users['student2@caldwell.edu']
        with harness.connect(self.uri) as setup:
            setup.execute("insert into storage.objects(bucket_id,name,owner_id) values('ticket-attachments',%s,%s)",(path,str(owner)))
        return self.c.execute("insert into public.attachments(ticket_id,uploaded_by,file_name,file_url,file_type,file_size_bytes) "
            "values(%s,%s,'fixture.png',%s,'image/png',%s) returning attachment_id",
            (t,owner,path,len(self.fx.png()))).fetchone()['attachment_id']

    def discover(self,after=0,limit=50,c=None):
        return (c or self.c).execute('select public.vision_shadow_discover(%s,%s) as r',(after,limit)).fetchone()['r']

    def claim(self,c=None,lease=120):
        from psycopg.types.json import Jsonb
        return (c or self.c).execute('select public.vision_shadow_claim(%s,%s,%s) as r',
            ('fixture-worker',lease,Jsonb(self.contract.PROVENANCE))).fetchone()['r']

    def finish(self,claim,*,error=None,retry=False,c=None):
        from psycopg.types.json import Jsonb
        return (c or self.c).execute('select public.vision_shadow_finish(%s,%s,%s,%s,%s,%s) as r',
            (claim['ticket_id'],claim['token'],Jsonb({'fixture_only':True}),Jsonb(self.contract.PROVENANCE),error,retry)).fetchone()['r']

    def job(self,t):
        return self.c.execute('select * from public.ticket_vision_jobs where ticket_id=%s',(t,)).fetchone()

    def runs(self,t):
        return self.c.execute('select * from public.ticket_vision_runs where ticket_id=%s order by generation,attempt',(t,)).fetchall()

    def due(self,t):
        self.c.execute("update public.ticket_vision_jobs set next_attempt_at=clock_timestamp()-interval '1 second' where ticket_id=%s",(t,))

    def queued(self,**kwargs):
        t=self.new_ticket(**kwargs);self.attach(t);self.discover();return t

    def test_discovery_is_idempotent_and_late_attachment_creates_new_generation(self):
        t=self.queued();first=self.claim();self.finish(first)
        self.discover();self.assertFalse(self.claim()['claimed'])
        self.attach(t);self.discover()
        second=self.claim();self.assertTrue(second['claimed'])
        self.assertEqual((second['ticket_id'],second['generation']),(t,2))
        self.assertEqual(len(second['attachments']),2)
        self.finish(second);self.assertEqual(len(self.runs(t)),2)

    def test_two_workers_only_one_claim_and_one_durable_result(self):
        t=self.queued();barrier=threading.Barrier(2)
        def claim():
            c=self.fresh();barrier.wait();return self.claim(c)
        with ThreadPoolExecutor(2) as pool: claims=list(pool.map(lambda _:claim(),range(2)))
        self.assertEqual(sum(c['claimed'] for c in claims),1)
        winner=next(c for c in claims if c['claimed'])
        self.assertEqual(self.finish(winner)['result'],'SUCCEEDED')
        self.assertEqual(self.finish(winner)['result'],'LEASE_LOST')
        self.assertEqual(len(self.runs(t)),1)

    def test_locked_oldest_ticket_does_not_starve_later_work(self):
        oldest=self.queued();newest=self.queued()
        blocker=self.fresh();blocker.execute('begin')
        try:
            blocker.execute('select 1 from public.tickets where ticket_id=%s for update',(oldest,))
            claimed=self.claim();self.assertEqual(claimed['ticket_id'],newest)
        finally: blocker.execute('rollback')

    def test_expired_worker_is_fenced_and_crash_backoff_is_bounded(self):
        t=self.queued()
        for i in range(3):
            claim=self.claim();self.assertTrue(claim['claimed'])
            self.c.execute("update public.ticket_vision_jobs set claimed_until=clock_timestamp()-interval '1 second' where ticket_id=%s",(t,))
            self.assertEqual(self.finish(claim)['result'],'LEASE_LOST')
            self.assertFalse(self.claim()['claimed']) # reaper, not an immediate retry
            if i<2:
                self.assertEqual(self.job(t)['status'],'PENDING')
                self.assertFalse(self.claim()['claimed']);self.due(t)
        self.assertEqual(self.job(t)['status'],'REVIEW')
        self.assertEqual([r['status'] for r in self.runs(t)],['CRASHED']*3)

    def test_handled_transient_errors_back_off_then_reach_human_review(self):
        t=self.queued()
        for i in range(3):
            claim=self.claim();finished=self.finish(claim,error='PROVIDER_UNAVAILABLE',retry=True)
            self.assertEqual(finished['result'],'RETRY' if i<2 else 'REVIEW')
            if i<2:
                self.assertFalse(self.claim()['claimed']);self.due(t)
        self.assertEqual(self.job(t)['attempts'],3)
        self.assertIsNone(self.job(t)['next_attempt_at'])

    def test_new_attachment_or_report_edit_invalidates_inflight_result(self):
        for change in ('attachment','text'):
            t=self.queued();claim=self.claim()
            if change=='attachment': self.attach(t)
            else: self.c.execute("update public.tickets set original_text='Unit fixture changed report' where ticket_id=%s",(t,))
            self.assertEqual(self.finish(claim)['result'],'SUPERSEDED')
            self.assertIsNone(self.runs(t)[0]['findings'])
            self.discover();self.assertEqual(self.job(t)['generation'],2)
            self.finish(self.claim()) # complete the new generation before the next fixture

    def test_discovery_fences_inflight_old_generation(self):
        t=self.queued();claim=self.claim();self.attach(t);self.discover()
        fresh=self.claim()
        self.assertEqual(self.finish(claim)['result'],'LEASE_LOST')
        self.assertEqual(self.finish(fresh)['result'],'SUCCEEDED')
        self.assertEqual([r['status'] for r in self.runs(t)],['SUPERSEDED','SUCCEEDED'])

    def test_text_baseline_change_and_storage_object_change_invalidate_fusion(self):
        t=self.queued();claim=self.claim()
        self.c.execute('update public.tickets set ai_suggested_priority_id=%s where ticket_id=%s',(self.prio['URGENT'],t))
        self.assertEqual(self.finish(claim)['result'],'SUPERSEDED')
        self.discover();claim=self.claim()
        with harness.connect(self.uri) as owner:
            owner.execute("update storage.objects set created_at=clock_timestamp() where name like %s",(f'{t}/%',))
        self.assertEqual(self.finish(claim)['result'],'SUPERSEDED')

    def test_concurrent_discovery_creates_one_generation(self):
        t=self.new_ticket();self.attach(t);barrier=threading.Barrier(2)
        def discover():
            conn=self.fresh();barrier.wait();return self.discover(c=conn)
        with ThreadPoolExecutor(2) as pool: list(pool.map(lambda _:discover(),range(2)))
        self.assertEqual(self.job(t)['generation'],1)
        self.assertEqual(self.c.execute('select count(*) as n from public.ticket_vision_jobs').fetchone()['n'],1)

    def test_withdrawal_prevents_result_and_stops_retry(self):
        t=self.queued();claim=self.claim()
        self.c.execute('update public.tickets set deleted_at=clock_timestamp() where ticket_id=%s',(t,))
        self.assertEqual(self.finish(claim)['result'],'WITHDRAWN')
        self.assertIsNone(self.job(t)['next_attempt_at'])
        self.assertIsNone(self.runs(t)[0]['findings'])

    def test_removed_last_attachment_does_not_leave_an_accepted_current_result(self):
        t=self.queued();claim=self.claim()
        self.c.execute('delete from public.attachments where ticket_id=%s',(t,))
        self.discover();self.assertEqual(self.job(t)['generation'],2)
        self.assertEqual(self.finish(claim)['result'],'LEASE_LOST')

    def test_staff_routing_and_existing_emergency_pages_are_untouched(self):
        for emergency in (False,True):
            t=self.queued(emergency=emergency);claim=self.claim()
            if not emergency:
                self.c.execute("update public.tickets set department_id=%s,priority_id=%s,ai_routing_status='OVERRIDDEN' where ticket_id=%s",
                    (self.dept['IT'],self.prio['LOW'],t))
            before=self.ticket(t);pages=self.notifications(t)
            history=self.c.execute('select count(*) as n from public.ticket_history where ticket_id=%s',(t,)).fetchone()['n']
            self.finish(claim)
            self.assertEqual(self.ticket(t),before)
            self.assertEqual(self.notifications(t),pages)
            self.assertEqual(self.c.execute('select count(*) as n from public.ticket_history where ticket_id=%s',(t,)).fetchone()['n'],history)

    def test_anonymous_student_and_staff_cannot_read_write_or_execute_shadow(self):
        import psycopg
        for caller in ('student2@caldwell.edu','fac.staff@caldwell.edu','sys.admin@caldwell.edu'):
            conn=self.conn(caller)
            for sql in ('select * from public.ticket_vision_jobs','select * from public.ticket_vision_runs',
                        'select public.vision_shadow_discover()',
                        "select public.vision_shadow_claim('x',120,'{}')",
                        "select public.vision_shadow_finish(1,null,null,null)",
                        'delete from public.ticket_vision_jobs'):
                with self.subTest(caller=caller,sql=sql),self.assertRaises(psycopg.errors.InsufficientPrivilege): conn.execute(sql)
        conn=harness.connect(self.uri);self.addCleanup(conn.close);conn.execute('set role anon')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege): conn.execute('select public.vision_shadow_discover()')
        rls=self.c.execute("select relname,relrowsecurity from pg_class where relname in ('ticket_vision_jobs','ticket_vision_runs')").fetchall()
        self.assertTrue(all(r['relrowsecurity'] for r in rls))

    def test_real_worker_mock_request_provenance_fusion_and_no_ticket_writes(self):
        t=self.queued();before=self.ticket(t);pages=self.notifications(t)
        loader=type('FixtureLoader',(),{'load':lambda _,row,ticket:self.attachments.Image(row['attachment_id'],self.fx.png(),self.contract.sha(self.fx.png()),2,2)})()
        mock=self.worker.MockClaudeProvider(lambda req:self.fx.response(self.fx.result(
            visible_hazards=['broken_glass'],safety_escalation_suggested=True)))
        from app.config import Settings
        cfg=Settings(_env_file=None,supabase_url='http://localhost',supabase_publishable_key='fixture',vision_mode='shadow')
        outcome=asyncio.run(self.worker.tick(SqlVisionAdmin(self.uri),cfg,loader,mock))
        self.assertEqual(outcome['status'],'SUCCEEDED')
        run=self.runs(t)[0]
        self.assertEqual(run['provider'],'MOCK')
        self.assertEqual(len(run['provenance']['images']),1)
        self.assertTrue(run['findings']['fusion']['vision_hazard_observed'])
        self.assertFalse(run['findings']['fusion']['notifications_created'])
        self.assertNotIn('Unit fixture',json.dumps(run['findings']))
        self.assertEqual(self.ticket(t),before);self.assertEqual(self.notifications(t),pages)

    def test_commit_failure_and_ambiguous_response_do_not_duplicate_completed_work(self):
        t=self.queued();claim=self.claim()
        self.finish(claim) # Simulate response lost after committed transaction.
        self.assertEqual(self.finish(claim)['result'],'LEASE_LOST')
        self.discover();self.assertFalse(self.claim()['claimed'])
        self.assertEqual(len(self.runs(t)),1)

    def test_rolled_back_commit_can_be_retried_without_partial_result(self):
        t=self.queued();claim=self.claim();conn=self.fresh();conn.execute('begin')
        self.assertEqual(self.finish(claim,c=conn)['result'],'SUCCEEDED')
        conn.execute('rollback')
        self.assertEqual(self.runs(t)[0]['status'],'PROCESSING')
        self.assertIsNone(self.runs(t)[0]['findings'])
        self.assertEqual(self.finish(claim)['result'],'SUCCEEDED')
        self.assertEqual(len(self.runs(t)),1)

    def test_worker_deadline_fences_late_thread_before_retry(self):
        t=self.queued();release=threading.Event()
        loader=type('FixtureLoader',(),{'load':lambda _,row,ticket:self.attachments.Image(row['attachment_id'],self.fx.png(),self.contract.sha(self.fx.png()),2,2)})()
        def respond(req):
            release.wait(timeout=3)
            return self.fx.response()
        from app.config import Settings
        cfg=Settings(_env_file=None,supabase_url='http://localhost',supabase_publishable_key='fixture',vision_mode='shadow')
        async def exercise():
            try:
                return await self.worker.tick(SqlVisionAdmin(self.uri),cfg,loader,
                    self.worker.MockClaudeProvider(respond),timeout_seconds=0.05)
            finally: release.set()
        outcome=asyncio.run(exercise()) # waits for late executor thread on shutdown
        self.assertEqual(outcome['status'],'RETRY')
        self.assertEqual(self.runs(t)[0]['status'],'RETRY')
        self.assertIsNone(self.runs(t)[0]['findings'])
        self.assertEqual(self.job(t)['last_error'],'WORKER_TIMEOUT')

    def test_discovery_cursor_reaches_newer_tickets_and_wraps(self):
        ids=[]
        for _ in range(3):
            t=self.new_ticket();self.attach(t);ids.append(t)
        cursor=0
        for expected in ids:
            cursor=self.discover(cursor,1)['cursor'];self.assertEqual(cursor,expected)
        self.assertEqual(self.discover(cursor,1)['cursor'],0)
        self.assertEqual(self.c.execute('select count(*) as n from public.ticket_vision_jobs').fetchone()['n'],3)

    def test_excess_attachments_are_bounded_at_discovery_and_never_sent_to_provider(self):
        t=self.new_ticket()
        for _ in range(7): self.attach(t)
        self.discover();claim=self.claim()
        self.assertEqual(len(claim['attachments']),5) # rejection sentinel, not a partial analysis
        self.assertEqual(self.job(t)['input_snapshot']['attachment_count'],7)
        from unittest.mock import MagicMock
        provider=MagicMock()
        result=self.worker.process_claim(SqlVisionAdmin(self.uri),claim,MagicMock(),self.worker.MockClaudeProvider(provider))
        self.assertEqual(result['result'],'REVIEW')
        self.assertEqual(self.job(t)['last_error'],'ATTACHMENT_COUNT')
        provider.assert_not_called()
