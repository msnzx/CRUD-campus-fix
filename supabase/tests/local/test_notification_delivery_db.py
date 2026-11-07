"""Real PostgreSQL email leases, authorization, crash recovery and dispatch races."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness

SIGNATURES = {
    "email_claim_notification": [("p_lease_seconds", "integer")],
    "email_start_delivery": [("p_notification_id", "bigint"), ("p_claim_token", "uuid")],
    "email_finish_delivery": [("p_notification_id", "bigint"), ("p_claim_token", "uuid"),
                              ("p_sent", "boolean"), ("p_error", "text"), ("p_ambiguous", "boolean")],
    "email_retry_notification": [("p_notification_id", "bigint")],
}


class DbAdmin:
    """Only transport is replaced: execute the backend's exact RPC arguments."""
    def __init__(self, conn):
        self.conn = conn

    def rpc(self, name, params):
        def execute():
            args = [(key, typ) for key, typ in SIGNATURES[name] if key in params]
            sql = ", ".join(f"{key} => %s::{typ}" for key, typ in args)
            row = self.conn.execute(f"select public.{name}({sql}) as r",
                                    [params[key] for key, _ in args]).fetchone()
            return SimpleNamespace(data=row["r"])
        return SimpleNamespace(execute=execute)


@unittest.skipUnless(harness.available(), "pgserver/psycopg not installed")
class NotificationDeliveryDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uri = harness.create_database()
        sys.path.insert(0, str(harness.REPO / "backend"))
        from app import notify
        cls.notify = notify

    def setUp(self):
        self.c = self.connection()
        self.c.execute("delete from public.notifications")
        self.admin = DbAdmin(self.c)
        self.cfg = SimpleNamespace(app_url="http://localhost:3000")

    def connection(self, email=None):
        c = harness.connect(self.uri)
        harness.act_as(c, email)
        self.addCleanup(c.close)
        return c

    def seed(self, email="student2@caldwell.edu"):
        return self.c.execute("insert into public.notifications(user_id,type,message) "
                              "select user_id,'AI_URGENT','Safety alert' from public.users where email=%s "
                              "returning notification_id", (email,)).fetchone()["notification_id"]

    def claim(self, conn=None):
        return DbAdmin(conn or self.c).rpc("email_claim_notification", {"p_lease_seconds": 120}).execute().data

    def start(self, claim, conn=None):
        return DbAdmin(conn or self.c).rpc("email_start_delivery", self.identity(claim)).execute().data

    @staticmethod
    def identity(claim):
        return {"p_notification_id": claim["notification_id"], "p_claim_token": claim["claim_token"]}

    def finish(self, claim, sent=True, ambiguous=False, conn=None):
        return DbAdmin(conn or self.c).rpc("email_finish_delivery", {
            **self.identity(claim), "p_sent": sent, "p_error": "test failure", "p_ambiguous": ambiguous,
        }).execute().data

    def row(self, notification_id):
        return self.c.execute("select * from public.notifications where notification_id=%s",
                              (notification_id,)).fetchone()

    def expire(self, notification_id):
        self.c.execute("update public.notifications set email_claimed_until=clock_timestamp()-interval '1 second' "
                       "where notification_id=%s", (notification_id,))

    def due(self, notification_id):
        self.c.execute("update public.notifications set email_next_attempt_at=clock_timestamp()-interval '1 second' "
                       "where notification_id=%s", (notification_id,))

    def parallel(self, action, workers=6):
        barrier = threading.Barrier(workers)
        def run(_):
            with harness.connect(self.uri) as c:
                harness.act_as(c, None)
                barrier.wait(timeout=10)
                return action(c)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(run, range(workers)))

    def test_concurrent_claims_grant_one_lease_and_count_one_attempt(self):
        n = self.seed()
        results = self.parallel(self.claim)
        self.assertEqual(sum(r["claimed"] for r in results), 1)
        self.assertEqual((self.row(n)["attempts"], self.row(n)["email_total_attempts"]), (1, 1))

    def test_concurrent_backend_dispatch_sends_once(self):
        n = self.seed()
        calls = []
        def send(*args, **kwargs):
            calls.append(kwargs["message_id"])
            time.sleep(0.2)
        with patch.object(self.notify, "_send", side_effect=send):
            results = self.parallel(lambda c: self.notify.dispatch_pending(DbAdmin(c), self.cfg))
        self.assertEqual(len(calls), 1)
        self.assertEqual(sum(r["sent"] for r in results), 1)
        self.assertEqual(self.row(n)["delivery_status"], "SENT")

    def test_skip_locked_row_does_not_block_other_notifications(self):
        first, second = self.seed(), self.seed()
        locker = self.connection()
        locker.execute("begin")
        locker.execute("select * from public.notifications where notification_id=%s for update", (first,))
        c = self.connection()
        c.execute("set statement_timeout='2s'")
        self.assertEqual(self.claim(c)["notification_id"], second)
        locker.execute("rollback")

    def test_crash_before_start_recovers_and_old_worker_is_fenced(self):
        n = self.seed()
        old = self.claim()
        self.expire(n)
        self.assertFalse(self.claim()["claimed"])
        row = self.row(n)
        self.assertEqual((row["delivery_status"], row["email_ambiguous_attempts"]), ("FAILED", 0))
        self.assertIn("before SMTP", row["error_message"])
        self.due(n)
        new = self.claim()
        self.assertNotEqual(old["claim_token"], new["claim_token"])
        self.assertFalse(self.start(old)["started"])
        self.assertFalse(self.finish(old))
        self.assertTrue(self.start(new)["started"])
        self.assertFalse(self.start(new)["started"])
        self.assertTrue(self.finish(new))
        self.assertTrue(self.finish(new))  # lost acknowledgement response
        self.assertEqual(self.row(n)["email_total_attempts"], 2)

    def test_crash_after_start_records_ambiguity_and_retry_is_not_dropped(self):
        n = self.seed()
        old = self.claim()
        self.start(old)
        self.expire(n)
        self.claim()
        self.assertEqual(self.row(n)["email_ambiguous_attempts"], 1)
        self.assertIn("unknown", self.row(n)["error_message"])
        self.due(n)
        new = self.claim()
        self.assertFalse(self.finish(old))
        self.start(new)
        self.assertTrue(self.finish(new))
        self.assertEqual(self.row(n)["email_ambiguous_attempts"], 1)

    def test_late_success_before_reaper_avoids_resend(self):
        n = self.seed()
        claim = self.claim()
        self.start(claim)
        self.expire(n)
        self.assertTrue(self.finish(claim))
        self.assertFalse(self.claim()["claimed"])

    def test_final_attempt_crash_exhaustion_and_explicit_recovery(self):
        n = self.seed()
        self.c.execute("update public.notifications set attempts=4,email_total_attempts=4 where notification_id=%s", (n,))
        claim = self.claim()
        self.start(claim)
        self.expire(n)
        self.assertFalse(self.claim()["claimed"])
        row = self.row(n)
        self.assertEqual((row["delivery_status"], row["attempts"], row["email_total_attempts"]), ("FAILED", 5, 5))
        self.assertIn("exhausted", row["error_message"])
        self.assertTrue(self.admin.rpc("email_retry_notification", {"p_notification_id": n}).execute().data)
        recovered = self.claim()
        self.assertEqual((self.row(n)["attempts"], self.row(n)["email_total_attempts"]), (1, 6))
        self.assertEqual(self.row(n)["email_manual_retries"], 1)
        self.assertFalse(self.admin.rpc("email_retry_notification", {"p_notification_id": n}).execute().data)
        self.start(recovered)
        self.finish(recovered)
        self.assertFalse(self.admin.rpc("email_retry_notification", {"p_notification_id": n}).execute().data)

    def test_retry_backoff_and_stable_message_id(self):
        n = self.seed()
        ids = []
        def send(*args, **kwargs):
            ids.append(kwargs["message_id"])
            if len(ids) == 1:
                raise self.notify.EmailDeliveryError("SMTP response lost", ambiguous=True)
        with patch.object(self.notify, "_send", side_effect=send):
            self.assertEqual(self.notify.dispatch_pending(self.admin, self.cfg)["failed"], 1)
            self.assertEqual(self.notify.dispatch_pending(self.admin, self.cfg)["sent"], 0)
            self.due(n)
            self.assertEqual(self.notify.dispatch_pending(self.admin, self.cfg)["sent"], 1)
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(self.row(n)["email_ambiguous_attempts"], 1)

    def test_inactive_recipient_is_not_emailed(self):
        n = self.seed()
        claim = self.claim()
        self.c.execute("update public.users set active=false where email='student2@caldwell.edu'")
        self.addCleanup(lambda: self.c.execute("update public.users set active=true where email='student2@caldwell.edu'"))
        row = self.start(claim)
        self.assertFalse(row["active"])
        self.finish(claim, sent=False)
        self.assertEqual(self.row(n)["delivery_status"], "FAILED")

    def test_email_rpcs_and_fields_are_not_client_writable(self):
        n = self.seed()
        for email in ("student2@caldwell.edu", "fac.staff@caldwell.edu", "sys.admin@caldwell.edu", "anon"):
            c = self.connection() if email == "anon" else self.connection(email)
            if email == "anon":
                c.execute("reset role")
                c.execute("set role anon")
            for fn, params in (
                ("email_claim_notification", {"p_lease_seconds": 120}),
                ("email_start_delivery", {"p_notification_id": n, "p_claim_token": None}),
                ("email_finish_delivery", {"p_notification_id": n, "p_claim_token": None, "p_sent": True}),
                ("email_retry_notification", {"p_notification_id": n}),
            ):
                with self.subTest(email=email, fn=fn), self.assertRaises(Exception):
                    DbAdmin(c).rpc(fn, params).execute()
            with self.assertRaises(Exception):
                c.execute("update public.notifications set email_claim_token=gen_random_uuid() where notification_id=%s", (n,))
        owner = self.connection("student2@caldwell.edu")
        self.assertEqual(owner.execute("select count(*) as n from public.notifications").fetchone()["n"], 1)
        owner.execute("update public.notifications set read_at=now() where notification_id=%s", (n,))
        outsider = self.connection("fac.staff@caldwell.edu")
        self.assertEqual(outsider.execute("select count(*) as n from public.notifications").fetchone()["n"], 0)

    def test_null_tokens_invalid_lease_and_finish_before_start_are_rejected(self):
        n = self.seed()
        for lease in (None, 0, 601):
            with self.assertRaises(Exception):
                self.admin.rpc("email_claim_notification", {"p_lease_seconds": lease}).execute()
        self.assertFalse(self.start({"notification_id": n, "claim_token": None})["started"])
        claim = self.claim()
        self.assertFalse(self.finish(claim))
        self.start(claim)
        self.assertTrue(self.finish(claim))

    def test_upgrade_preserves_legacy_sent_rows_attempts_and_backoff(self):
        migration = harness.MIGRATIONS / "20261106130000_notification_email_leases.sql"
        previous = [p for p in harness.migration_files() if p != migration]
        with patch.object(harness, "migration_files", return_value=previous):
            uri = harness.create_database()
        with harness.connect(uri) as c:
            c.execute("""insert into public.notifications(user_id,type,message,delivery_status,attempts,last_attempt_at)
                select user_id,'EMERGENCY',state,state,attempt,now()
                from public.users cross join (values ('PENDING',0),('FAILED',2),('SENT',3)) as v(state,attempt)
                where email='student2@caldwell.edu'""")
            c.execute(migration.read_text(encoding="utf-8"))
            rows = c.execute("select * from public.notifications order by notification_id").fetchall()
            self.assertEqual([r["email_total_attempts"] for r in rows], [0, 2, 3])
            self.assertEqual(len({r["email_message_id"] for r in rows}), 3)
            self.assertGreater(rows[1]["email_next_attempt_at"], rows[1]["last_attempt_at"])
            harness.act_as(c, None)
            self.assertEqual(self.claim(c)["notification_id"], rows[0]["notification_id"])
            self.assertFalse(self.claim(c)["claimed"])

    def test_existing_emergency_page_uses_leased_email_without_routing_changes(self):
        self.c.execute("insert into public.user_departments(user_id,department_id) "
                       "select u.user_id,d.department_id from public.users u cross join public.departments d "
                       "where u.email='it.staff@caldwell.edu' and d.name='Campus Safety'")
        self.addCleanup(lambda: self.c.execute("delete from public.user_departments "
                        "where user_id=(select user_id from public.users where email='it.staff@caldwell.edu') "
                        "and department_id=(select department_id from public.departments where name='Campus Safety')"))
        student = self.connection("student2@caldwell.edu")
        t = student.execute("insert into public.tickets(reported_by,title,original_text,issue_type_id,status_id,is_emergency) "
                            "values (auth.uid(),'Smoke','Smoke',"
                            "(select issue_type_id from public.issue_types where name='Electrical'),"
                            "(select status_id from public.statuses where name='NEW'),true) returning ticket_id").fetchone()["ticket_id"]
        before = self.c.execute("select department_id,priority_id,status_id,is_emergency from public.tickets "
                                "where ticket_id=%s", (t,)).fetchone()
        with patch.object(self.notify, "_send") as send:
            self.assertEqual(self.notify.dispatch_pending(self.admin, self.cfg)["sent"], 1)
            self.assertEqual(self.notify.dispatch_pending(self.admin, self.cfg)["sent"], 0)
        send.assert_called_once()
        after = self.c.execute("select department_id,priority_id,status_id,is_emergency from public.tickets "
                               "where ticket_id=%s", (t,)).fetchone()
        self.assertEqual(before, after)
        self.assertEqual(self.c.execute("select type,delivery_status from public.notifications "
                                        "where ticket_id=%s", (t,)).fetchone(),
                         {"type": "EMERGENCY", "delivery_status": "SENT"})
