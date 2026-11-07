"""Approved Batch 1 policy, real row locks, version fences, and crash recovery."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import harness
import test_ai_orchestration_db as fixtures
_SqlAdmin=fixtures._SqlAdmin


@unittest.skipUnless(harness.available(),"pgserver/psycopg not installed")
class Batch1DbTests(unittest.TestCase):
    # Share existing fixture helpers, without inheriting/rerunning its test cases.
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault('SUPABASE_URL','http://localhost')
        os.environ.setdefault('SUPABASE_PUBLISHABLE_KEY','test')
        fixtures.AiOrchestrationDbTests.setUpClass.__func__(cls)

    conn=fixtures.AiOrchestrationDbTests.conn
    svc=fixtures.AiOrchestrationDbTests.svc
    fresh=fixtures.AiOrchestrationDbTests.fresh
    new_ticket=fixtures.AiOrchestrationDbTests.new_ticket
    rpc=fixtures.AiOrchestrationDbTests.rpc
    claim=fixtures.AiOrchestrationDbTests.claim
    commit=fixtures.AiOrchestrationDbTests.commit
    ticket=fixtures.AiOrchestrationDbTests.ticket
    notifications=fixtures.AiOrchestrationDbTests.notifications
    parallel=fixtures.AiOrchestrationDbTests.parallel
    safety_member=fixtures.AiOrchestrationDbTests.safety_member

    def setUp(self):
        self.c=self.svc()
        # Each test's queue contains only tickets it creates.
        self.c.execute("update public.tickets set ai_routing_status='OVERRIDDEN'")

    def next(self,c=None,limit=3):
        return (c or self.c).execute("select public.ai_claim_next_ticket('test',60,%s,0) as r",(limit,)).fetchone()["r"]

    def queue(self,t):
        return self.c.execute("select * from public.ticket_safety_escalations where ticket_id=%s",(t,)).fetchone()

    def due(self,t):
        self.c.execute("update public.ticket_ai_jobs set next_attempt_at=clock_timestamp()-interval '1 second' where ticket_id=%s",(t,))

    def expire(self,t):
        self.c.execute("update public.ticket_ai_jobs set claimed_until=clock_timestamp()-interval '1 second' where ticket_id=%s",(t,))

    def enrich(self,t,result,c=None,**overrides):
        from psycopg.types.json import Jsonb
        text={"title":"Clean title","description":"Original facts preserved.",
              "original_text":self.ticket(t)["original_text"],"ai_explanation":"A plumbing issue."}
        text.update(overrides)
        return (c or self.c).execute("select public.ai_commit_enrichment(%s,%s::uuid,%s,%s) as r",
            (t,result["analysis_id"],result["enrichment_revision"],Jsonb(text))).fetchone()["r"]

    def transfer(self,t):
        return self.conn("fac.admin@caldwell.edu").execute("select public.transfer_ticket(%s,%s,'human decision')",(t,self.dept["IT"]))

    def test_other_urgent_real_backend_stays_review_and_records_version_reason(self):
        sys.path.insert(0,str(harness.REPO/'backend'))
        from app import classify as routing, main
        from app.config import Settings
        self.safety_member()
        t=self.new_ticket()
        routing._REF_CACHE=None
        cfg=Settings(_env_file=None,supabase_url='http://localhost',supabase_publishable_key='x',supabase_service_role_key='test')
        with patch.object(routing,'_classify_with_deepseek',return_value=('Other','URGENT')), \
             patch.object(main,'enrich_ticket') as enrichment:
            result=main._classify_pending_ticket(t,_SqlAdmin(self.uri),cfg)
        self.assertEqual(result.routing_status,'NEEDS_REVIEW')
        enrichment.assert_not_called()
        self.assertEqual(self.ticket(t)['ai_suggested_priority_id'],self.prio['URGENT'])
        self.assertEqual([n['user_id'] for n in self.notifications(t,'AI_URGENT')],[self.users['it.staff@caldwell.edu']])
        q=self.queue(t)
        self.assertIsNotNone(q['analysis_id'])
        self.assertEqual((q['analysis_version'],q['reason']),('campusfix-text-v2/safety-v1','TEXT_URGENT_OTHER_REVIEW'))
        history=self.c.execute("select new_value from public.ticket_history where ticket_id=%s and action='SAFETY_ESCALATED' order by history_id desc limit 1",(t,)).fetchone()
        evidence=json.loads(history['new_value'])
        self.assertEqual(evidence['analysis_id'],str(q['analysis_id']))
        self.assertEqual(evidence['reason'],q['reason'])

    def test_staff_transfer_kept_and_current_department_paged(self):
        self.safety_member()
        t=self.new_ticket()
        claim=self.claim(t)
        self.transfer(t)
        before=self.ticket(t)
        r=self.commit(t,claim['claim_token'],prio='URGENT')
        self.assertEqual(r['result'],'SUPERSEDED_BY_HUMAN')
        after=self.ticket(t)
        self.assertEqual([before[k] for k in ('department_id','priority_id','status_id')],
                         [after[k] for k in ('department_id','priority_id','status_id')])
        self.assertEqual([n['user_id'] for n in self.notifications(t,'AI_URGENT')],[self.users['it.staff@caldwell.edu']])
        self.assertEqual(self.queue(t)['reason'],'TEXT_URGENT_STAFF_ROUTING_PRESERVED')

    def test_staff_override_status_still_preserves_urgent(self):
        self.safety_member()
        t=self.new_ticket()
        token=self.claim(t)['claim_token']
        self.conn('fac.staff@caldwell.edu').execute("update public.tickets set priority_id=%s,ai_routing_status='OVERRIDDEN' where ticket_id=%s",(self.prio['LOW'],t))
        self.assertEqual(self.commit(t,token,prio='URGENT')['result'],'SUPERSEDED_BY_HUMAN')
        self.assertEqual(self.ticket(t)['priority_id'],self.prio['LOW'])
        self.assertEqual(len(self.notifications(t,'AI_URGENT')),4)

    def test_staff_transfer_of_emergency_notifies_new_team_without_repeating_emergency_pages(self):
        self.safety_member()
        self.c.execute('insert into public.user_departments(user_id,department_id) values (%s,%s)',
                       (self.users['fac.admin@caldwell.edu'],self.dept['Campus Safety']))
        self.addCleanup(lambda:self.c.execute('delete from public.user_departments where user_id=%s and department_id=%s',
            (self.users['fac.admin@caldwell.edu'],self.dept['Campus Safety'])))
        t=self.new_ticket(emergency=True)
        original_pages=self.notifications(t,'EMERGENCY')
        token=self.claim(t)['claim_token']
        self.conn('fac.admin@caldwell.edu').execute('select public.transfer_ticket(%s,%s)',(t,self.dept['Facilities']))
        self.assertEqual(self.commit(t,token,prio='URGENT')['result'],'SUPERSEDED_BY_HUMAN')
        self.assertEqual(self.ticket(t)['department_id'],self.dept['Facilities'])
        self.assertEqual(self.notifications(t,'EMERGENCY'),original_pages)
        new_pages=self.notifications(t,'AI_URGENT')
        self.assertEqual(len(new_pages),2)
        self.assertFalse({p['user_id'] for p in new_pages}&{p['user_id'] for p in original_pages})

    def test_changed_report_and_change_then_revert_invalidate_analysis(self):
        self.safety_member()
        for revert in (False,True):
            t=self.new_ticket()
            original=self.ticket(t)['title']
            token=self.claim(t)['claim_token']
            self.c.execute("update public.tickets set title='Changed facts' where ticket_id=%s",(t,))
            if revert:
                self.c.execute("update public.tickets set title=%s where ticket_id=%s",(original,t))
            self.transfer(t)
            self.assertEqual(self.commit(t,token,prio='URGENT')['result'],'STALE_SNAPSHOT')
            self.assertEqual(self.notifications(t,'AI_URGENT'),[])
            self.assertIsNone(self.queue(t))

    def test_withdrawal_and_expired_lease_cannot_page(self):
        self.safety_member()
        for action in ('withdraw','expire'):
            t=self.new_ticket()
            token=self.claim(t)['claim_token']
            if action=='withdraw':
                self.conn('student2@caldwell.edu').execute('select public.withdraw_own_ticket(%s)',(t,))
            else:
                self.expire(t)
            result=self.commit(t,token,prio='URGENT')
            self.assertIn(result['result'],('WITHDRAWN','LEASE_LOST'))
            self.assertEqual(self.notifications(t,'AI_URGENT'),[])

    def test_concurrent_urgent_commits_notify_each_recipient_once(self):
        self.safety_member()
        t=self.new_ticket()
        token=self.claim(t)['claim_token']
        result=self.parallel(lambda i:self.commit(t,token,prio='URGENT',c=self.fresh()),8)
        self.assertEqual(sum(r['result']=='COMMITTED_AUTO_ROUTED' for r in result),1)
        self.assertEqual(len(self.notifications(t,'AI_URGENT')),4)

    def test_pending_alert_cancelled_on_report_edit_or_explicit_reclassification(self):
        for action in ('edit','reclassify'):
            t=self.new_ticket()
            self.commit(t,self.claim(t)['claim_token'],prio='URGENT')
            self.assertEqual(self.queue(t)['status'],'FAILED') # no safety roster
            if action=='edit':
                self.c.execute("update public.tickets set description='New facts' where ticket_id=%s",(t,))
            else:
                self.rpc(self.svc(),'ai_request_reclassification',t,self.users['fac.admin@caldwell.edu'])
            self.c.execute('select public.escalate_ticket_safety(%s)',(t,))
            self.assertEqual(self.queue(t)['status'],'CANCELLED')
            self.assertEqual(self.notifications(t,'AI_URGENT'),[])

    def test_pending_recovery_follows_department_transfer(self):
        t=self.new_ticket()
        self.commit(t,self.claim(t)['claim_token'],prio='URGENT')
        self.transfer(t)
        self.safety_member()
        self.c.execute('select public.escalate_ticket_safety(%s)',(t,))
        self.assertEqual([n['user_id'] for n in self.notifications(t,'AI_URGENT')],[self.users['it.staff@caldwell.edu']])

    def test_later_valid_analysis_notifies_new_department_without_repeating_safety(self):
        self.safety_member()
        with harness.connect(self.uri) as setup:
            user=setup.execute("insert into auth.users(email) values ('batch1.res@caldwell.edu') returning id").fetchone()['id']
            setup.execute("update public.users set role_id=(select role_id from public.roles where name='DEPARTMENT_STAFF') where user_id=%s",(user,))
            setup.execute('insert into public.user_departments(user_id,department_id) values (%s,%s)',(user,self.dept['Residence Life']))
        t=self.new_ticket()
        self.commit(t,self.claim(t)['claim_token'],prio='URGENT')
        self.conn('fac.admin@caldwell.edu').execute("select public.transfer_ticket(%s,%s,'new owner')",(t,self.dept['Residence Life']))
        self.rpc(self.svc(),'ai_request_reclassification',t,user)
        self.commit(t,self.claim(t)['claim_token'],dept='Residence Life',prio='URGENT')
        pages=self.notifications(t,'AI_URGENT')
        self.assertEqual(len(pages),5)
        self.assertEqual(sum(n['user_id']==user for n in pages),1)

    def test_guarded_enrichment_one_use_and_original_fields_preserved(self):
        t=self.new_ticket()
        r=self.commit(t,self.claim(t)['claim_token'])
        before=self.ticket(t)
        self.assertTrue(self.enrich(t,r))
        self.assertFalse(self.enrich(t,r))
        after=self.ticket(t)
        for field in ('original_title','original_text','department_id','priority_id','status_id'):
            self.assertEqual(before[field],after[field])

    def test_staff_decision_and_new_analysis_fence_enrichment(self):
        for action in ('priority','transfer','report','reclassify'):
            t=self.new_ticket()
            r=self.commit(t,self.claim(t)['claim_token'])
            if action=='priority':
                self.conn('fac.staff@caldwell.edu').execute('update public.tickets set priority_id=%s where ticket_id=%s',(self.prio['LOW'],t))
            elif action=='transfer':
                self.transfer(t)
            elif action=='report':
                self.c.execute("update public.tickets set description='Staff corrected facts' where ticket_id=%s",(t,))
            else:
                self.rpc(self.svc(),'ai_request_reclassification',t,self.users['fac.admin@caldwell.edu'])
            before=self.ticket(t)
            self.assertFalse(self.enrich(t,r))
            self.assertEqual(self.ticket(t),before)

    def test_concurrent_enrichments_commit_once(self):
        t=self.new_ticket()
        r=self.commit(t,self.claim(t)['claim_token'])
        results=self.parallel(lambda i:self.enrich(t,r,c=self.fresh()),6)
        self.assertEqual(sum(results),1)

    def test_assignment_history_fences_enrichment_even_without_ticket_update(self):
        t=self.new_ticket()
        r=self.commit(t,self.claim(t)['claim_token'])
        before=self.ticket(t)
        self.conn('fac.admin@caldwell.edu').execute('select public.assign_ticket(%s,%s)',
            (t,self.users['fac.staff@caldwell.edu']))
        self.assertEqual(self.ticket(t)['ai_revision'],before['ai_revision'])
        self.assertFalse(self.enrich(t,r))

    def test_enrichment_waits_for_staff_transaction_then_rejects(self):
        t=self.new_ticket()
        r=self.commit(t,self.claim(t)['claim_token'])
        staff=self.conn('fac.staff@caldwell.edu')
        staff.execute('begin')
        staff.execute('update public.tickets set priority_id=%s where ticket_id=%s',(self.prio['LOW'],t))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(lambda:self.enrich(t,r,c=self.fresh()))
            time.sleep(0.1)
            self.assertFalse(future.done())
            staff.execute('commit')
            self.assertFalse(future.result(timeout=10))
        self.assertEqual(self.ticket(t)['priority_id'],self.prio['LOW'])

    def test_own_enrichment_does_not_lose_pending_safety_recovery(self):
        t=self.new_ticket()
        r=self.commit(t,self.claim(t)['claim_token'],prio='URGENT')
        self.assertEqual(self.queue(t)['status'],'FAILED')
        self.assertTrue(self.enrich(t,r))
        self.assertEqual(self.queue(t)['report_revision'],self.ticket(t)['ai_report_revision'])
        self.safety_member()
        self.c.execute('select public.escalate_ticket_safety(%s)',(t,))
        self.assertEqual(len(self.notifications(t,'AI_URGENT')),4)

    def test_enrichment_cannot_change_original_report_or_add_fields(self):
        t=self.new_ticket()
        r=self.commit(t,self.claim(t)['claim_token'])
        self.assertFalse(self.enrich(t,r,original_text='Invented original'))
        self.assertFalse(self.enrich(t,r,priority_id=self.prio['URGENT']))

    def test_oldest_due_work_and_concurrent_claims_are_distinct(self):
        tickets=[self.new_ticket() for _ in range(6)]
        for i,t in enumerate(tickets):
            self.c.execute("update public.tickets set created_at=clock_timestamp()-make_interval(secs=>%s) where ticket_id=%s",(100-i,t))
        first=self.next()
        self.assertEqual(first['ticket']['ticket_id'],tickets[0])
        rest=self.parallel(lambda i:self.next(c=self.fresh()),5)
        self.assertEqual({r['ticket']['ticket_id'] for r in rest},set(tickets[1:]))

    def _force_stale_queue_selection(self,change):
        """Hold only the job row to pause a selector after its ticket snapshot.

        This deliberate fault injection forces the state a fast concurrent
        claim/commit can expose through a stale LEFT JOIN snapshot.
        """
        import uuid
        oldest,newest=self.new_ticket(),self.new_ticket()
        self.c.execute('insert into public.ticket_ai_jobs(ticket_id) values(%s)',(oldest,))
        blocker=self.fresh();blocker.execute('begin')
        blocker.execute('select 1 from public.ticket_ai_jobs where ticket_id=%s for update',(oldest,))
        selector=self.fresh();pid=selector.execute('select pg_backend_pid() as p').fetchone()['p']
        monitor=harness.connect(self.uri);self.addCleanup(monitor.close)
        token=str(uuid.uuid4())
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.next,selector)
            try:
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    wait=monitor.execute('select wait_event_type from pg_stat_activity where pid=%s',(pid,)).fetchone()
                    if wait and wait['wait_event_type']=='Lock': break
                    time.sleep(0.01)
                else: self.fail('Selector did not reach the forced job-lock wait')
                if change=='lease':
                    blocker.execute("""update public.ticket_ai_jobs j set claim_token=%s::uuid,
                        claimed_until=clock_timestamp()+interval '1 minute',claimed_by='fixture-live-worker',attempts=1,
                        snapshot_updated_at=t.updated_at,snapshot_revision=t.ai_revision,
                        snapshot_report_revision=t.ai_report_revision
                        from public.tickets t where j.ticket_id=t.ticket_id and j.ticket_id=%s""",(token,oldest))
                else:
                    blocker.execute("update public.ticket_ai_jobs set next_attempt_at=clock_timestamp()+interval '1 hour' where ticket_id=%s",(oldest,))
            finally:
                blocker.execute('commit')
            result=future.result(timeout=10)
        return oldest,newest,token,result

    def test_stale_queue_snapshot_must_not_reap_a_live_worker_lease(self):
        oldest,newest,token,result=self._force_stale_queue_selection('lease')
        job=self.c.execute('select * from public.ticket_ai_jobs where ticket_id=%s',(oldest,)).fetchone()
        self.assertEqual(str(job['claim_token']),token)
        self.assertEqual(job['attempts'],1)
        self.assertTrue(result['claimed'])
        self.assertEqual(result['ticket']['ticket_id'],newest)
        self.assertEqual(self.commit(oldest,token)['result'],'COMMITTED_AUTO_ROUTED')

    def test_stale_queue_snapshot_must_not_bypass_new_backoff(self):
        oldest,newest,token,result=self._force_stale_queue_selection('backoff')
        self.assertTrue(result['claimed'])
        self.assertEqual(result['ticket']['ticket_id'],newest)
        job=self.c.execute('select * from public.ticket_ai_jobs where ticket_id=%s',(oldest,)).fetchone()
        self.assertEqual(job['attempts'],0)
        self.assertIsNone(job['claim_token'])

    def test_failed_jobs_back_off_retry_then_exhaust(self):
        t=self.new_ticket()
        for attempt in (1,2,3):
            c=self.next()
            self.assertEqual(c['attempts'],attempt)
            self.commit(t,c['claim_token'],outcome='FAILED',dept=None,issue=None,prio=None,conf=None)
            self.assertEqual(self.next()['reason'],'empty')
            if attempt<3:
                self.due(t)
        j=self.c.execute('select * from public.ticket_ai_jobs where ticket_id=%s',(t,)).fetchone()
        self.assertIsNone(j['next_attempt_at'])
        self.assertIsNotNone(j['last_error'])
        self.assertEqual(self.ticket(t)['status_name'],'NEEDS_REVIEW')

    def test_final_release_preserves_staff_routing_and_stops_automatic_retries(self):
        t=self.new_ticket()
        claim=self.next(limit=1)
        self.transfer(t)
        before=self.ticket(t)
        self.assertTrue(self.rpc(self.svc(),'ai_release_claim',t,claim['claim_token'],'injected crash boundary'))
        after=self.ticket(t)
        for field in ('department_id','priority_id','status_id'):
            self.assertEqual(before[field],after[field])
        self.assertEqual(after['ai_routing_status'],'OVERRIDDEN')
        self.assertEqual(self.next()['reason'],'empty')

    def test_final_stale_snapshot_reaches_review_without_alert(self):
        t=self.new_ticket()
        claim=self.next(limit=1)
        self.c.execute('update public.tickets set location_id=location_id where ticket_id=%s',(t,))
        result=self.commit(t,claim['claim_token'],prio='URGENT')
        self.assertEqual(result['result'],'STALE_SNAPSHOT')
        self.assertEqual(self.ticket(t)['ai_routing_status'],'FAILED')
        self.assertEqual(self.next()['reason'],'empty')
        self.assertEqual(self.notifications(t,'AI_URGENT'),[])

    def test_crash_reaped_with_backoff_and_old_token_fenced(self):
        t=self.new_ticket()
        first=self.next()
        self.expire(t)
        self.assertEqual(self.next()['reason'],'crash_backoff')
        self.assertEqual(self.commit(t,first['claim_token'])['result'],'LEASE_LOST')
        self.assertEqual(self.next()['reason'],'empty')
        self.due(t)
        second=self.next()
        self.assertEqual(second['attempts'],2)
        self.assertNotEqual(first['claim_token'],second['claim_token'])

    def test_final_crash_exits_pending_and_manual_reclassification_recovers(self):
        t=self.new_ticket()
        self.next(limit=1)
        self.expire(t)
        self.next(limit=1)
        self.assertEqual(self.ticket(t)['ai_routing_status'],'FAILED')
        self.assertEqual(self.next()['reason'],'empty')
        self.rpc(self.svc(),'ai_request_reclassification',t,self.users['fac.admin@caldwell.edu'])
        self.assertTrue(self.next()['claimed'])

    def test_worker_skips_locked_oldest_ticket(self):
        first,second=self.new_ticket(),self.new_ticket()
        locker=self.svc()
        locker.execute('begin')
        locker.execute('select * from public.tickets where ticket_id=%s for update',(first,))
        try:
            self.c.execute("set statement_timeout='2s'")
            self.assertEqual(self.next()['ticket']['ticket_id'],second)
        finally:
            locker.execute('rollback')

    def test_new_rpcs_are_service_only_and_revision_counters_cannot_be_forged(self):
        t=self.new_ticket()
        staff=self.conn('fac.staff@caldwell.edu')
        old=self.ticket(t)
        staff.execute('update public.tickets set ai_revision=999,ai_report_revision=999 where ticket_id=%s',(t,))
        row=self.ticket(t)
        self.assertEqual((row['ai_revision'],row['ai_report_revision']),(old['ai_revision']+1,old['ai_report_revision']))
        for email in ('student2@caldwell.edu','fac.staff@caldwell.edu','sys.admin@caldwell.edu','anon'):
            c=self.svc() if email=='anon' else self.conn(email)
            if email=='anon':
                c.execute('reset role')
                c.execute('set role anon')
            with self.assertRaises(Exception):
                self.next(c=c)
            with self.assertRaises(Exception):
                c.execute("select public.ai_commit_enrichment(%s,null,0,'{}')",(t,))
