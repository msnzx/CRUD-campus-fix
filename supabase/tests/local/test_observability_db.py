"""Real PostgreSQL service-only monitoring, stale workers and read-only queues."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
import harness


@unittest.skipUnless(harness.available(),'pgserver/psycopg not installed')
class ObservabilityDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uri=harness.create_database()
        with harness.connect(cls.uri) as conn:
            conn.execute((harness.REPO/'supabase/optional/batch6_observability.sql').read_text(encoding='utf-8'))

    def connection(self,email=None):
        conn=harness.connect(self.uri);harness.act_as(conn,email);self.addCleanup(conn.close);return conn

    def test_clients_cannot_read_write_or_call_aggregate_rpc(self):
        import psycopg
        for email in harness.ACCOUNTS:
            c=self.connection(email)
            for sql in ('select * from public.observability_workers',
                        'select public.observability_snapshot()',
                        'delete from public.observability_workers'):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege): c.execute(sql)
        c=self.connection();c.execute('set role anon')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):c.execute('select public.observability_snapshot()')

    def test_new_table_enables_rls_and_rpc_is_security_invoker(self):
        c=self.connection();row=c.execute("select relrowsecurity from pg_class where oid='public.observability_workers'::regclass").fetchone()
        self.assertTrue(row['relrowsecurity'])
        self.assertFalse(c.execute("select prosecdef from pg_proc where oid='public.observability_snapshot()'::regprocedure").fetchone()['prosecdef'])

    def test_empty_queue_counts_are_measured_and_no_content_is_returned(self):
        c=self.connection();data=c.execute('select public.observability_snapshot() as result').fetchone()['result']
        self.assertEqual(set(data),{'observed_at','classification','email','safety','workers','workers_truncated'})
        for key in ('classification','email','safety'):
            self.assertEqual(data[key]['pending'],0);self.assertEqual(data[key]['due'],0)
        self.assertNotIn('description',str(data));self.assertNotIn('message',str(data))

    def test_stale_durable_worker_never_appears_healthy(self):
        c=self.connection();c.execute('reset role');identity=uuid.uuid4()
        c.execute('alter table public.observability_workers disable trigger observability_receipt_time')
        self.addCleanup(lambda:c.execute('alter table public.observability_workers enable trigger observability_receipt_time'))
        c.execute("insert into public.observability_workers values (%s,'classification',true,'HEALTHY',clock_timestamp(),clock_timestamp()-interval '2 minutes')",(identity,))
        data=c.execute('select public.observability_snapshot() as r').fetchone()['r']
        row=next(w for w in data['workers'] if w['instance_id']==str(identity))
        self.assertEqual(row['state'],'STALE')

    def test_actual_email_due_exhausted_and_ambiguous_counts(self):
        c=self.connection()
        try:
            for attempts,ambiguous in ((0,0),(5,1)):
                c.execute("insert into public.notifications(user_id,type,message,attempts,email_ambiguous_attempts) select user_id,'AI_URGENT','PRIVATE TEST CONTENT',%s,%s from public.users where email='student2@caldwell.edu'",(attempts,ambiguous))
            data=c.execute('select public.observability_snapshot() as r').fetchone()['r']['email']
            self.assertEqual(data['due'],1);self.assertEqual(data['exhausted'],1);self.assertEqual(data['ambiguous'],1)
        finally:c.execute("delete from public.notifications where message='PRIVATE TEST CONTENT'")

    def test_concurrent_observers_do_not_duplicate_workers(self):
        identity=uuid.uuid4()
        def write(_):
            with harness.connect(self.uri) as conn:
                harness.act_as(conn,None)
                conn.execute("insert into public.observability_workers(instance_id,worker,enabled,state) values (%s,'safety_retry',true,'ALIVE') on conflict(instance_id,worker) do update set observed_at=clock_timestamp()",(identity,))
        with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(write,range(24)))
        self.assertEqual(self.connection().execute('select count(*) as n from public.observability_workers where instance_id=%s',(identity,)).fetchone()['n'],1)

    def test_receipt_clock_cannot_be_forged_by_a_worker(self):
        c=self.connection();identity=uuid.uuid4()
        row=c.execute("insert into public.observability_workers(instance_id,worker,enabled,state,observed_at) values (%s,'vision',false,'DISABLED','2099-01-01') returning abs(extract(epoch from observed_at-clock_timestamp())) as delta",(identity,)).fetchone()
        self.assertLess(row['delta'],2)

    def test_classification_due_matches_age_gate_and_terminal_is_separate(self):
        c=self.connection('student2@caldwell.edu')
        ticket=c.execute("insert into public.tickets(reported_by,original_text,title,issue_type_id,status_id) values ((select auth.uid()),'Fixture','Fixture',(select issue_type_id from public.issue_types where name='Plumbing'),(select status_id from public.statuses where name='NEW')) returning ticket_id").fetchone()['ticket_id']
        svc=self.connection()
        try:
            data=svc.execute('select public.observability_snapshot() as r').fetchone()['r']['classification']
            self.assertEqual(data['pending'],1);self.assertEqual(data['due'],0)
            svc.execute("update public.tickets set created_at=clock_timestamp()-interval '1 minute' where ticket_id=%s",(ticket,))
            self.assertEqual(svc.execute('select public.observability_snapshot() as r').fetchone()['r']['classification']['due'],1)
            svc.execute('insert into public.ticket_ai_jobs(ticket_id,next_attempt_at) values (%s,null)',(ticket,))
            data=svc.execute('select public.observability_snapshot() as r').fetchone()['r']['classification']
            self.assertEqual(data['terminal'],1);self.assertEqual(data['due'],0)
        finally:svc.execute("update public.tickets set ai_routing_status='OVERRIDDEN' where ticket_id=%s",(ticket,))
