"""Offline SMTP and dispatcher regressions. No mail server is contacted."""
import smtplib
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app import notify


class SmtpTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(smtp_from="CampusFix <alerts@example.test>",
                                   smtp_host="localhost", smtp_port=587,
                                   smtp_user="", smtp_password="")
        self.smtp = MagicMock()
        self.smtp.send_message.return_value = {}
        self.factory = patch.object(notify.smtplib, "SMTP", return_value=self.smtp).start()
        self.addCleanup(patch.stopall)

    def send(self):
        notify._send(self.cfg, "staff@example.test", "Alert", "Review ticket",
                     message_id="<campusfix-notification-1@notifications.campusfix.invalid>")

    def test_stable_message_id_and_verified_tls(self):
        self.send()
        message = self.smtp.send_message.call_args.args[0]
        self.assertEqual(message["Message-ID"], "<campusfix-notification-1@notifications.campusfix.invalid>")
        self.assertTrue(self.smtp.starttls.call_args.kwargs["context"].check_hostname)

    def test_quit_failure_after_acceptance_does_not_retry_mail(self):
        self.smtp.quit.side_effect = smtplib.SMTPServerDisconnected("QUIT response lost")
        self.send()
        self.smtp.send_message.assert_called_once()
        self.smtp.close.assert_called_once()

    def test_transport_loss_during_data_is_ambiguous(self):
        self.smtp.send_message.side_effect = smtplib.SMTPServerDisconnected("DATA response lost")
        with self.assertRaises(notify.EmailDeliveryError) as error:
            self.send()
        self.assertTrue(error.exception.ambiguous)

    def test_explicit_rejection_is_not_ambiguous(self):
        self.smtp.send_message.side_effect = smtplib.SMTPDataError(550, b"Rejected")
        with self.assertRaises(notify.EmailDeliveryError) as error:
            self.send()
        self.assertFalse(error.exception.ambiguous)

    def test_connection_failure_precedes_smtp_delivery(self):
        self.factory.side_effect = OSError("Connection refused")
        with self.assertRaises(notify.EmailDeliveryError) as error:
            self.send()
        self.assertFalse(error.exception.ambiguous)
        self.smtp.send_message.assert_not_called()


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.admin = MagicMock()
        self.cfg = SimpleNamespace(app_url="http://localhost:3000")
        self.calls = []
        self.claims = [{"claimed": True, "notification_id": 1, "claim_token": "token"}, {"claimed": False}]
        self.row = {"started": True, "ticket_id": 12, "message": "Safety alert",
                    "active": True, "email": "staff@example.test", "attempts": 1,
                    "email_message_id": "test-message-id"}
        self.finish_results = [True]
        def rpc(name, params):
            self.calls.append((name, dict(params)))
            def execute():
                if name == "email_claim_notification":
                    return SimpleNamespace(data=self.claims.pop(0))
                if name == "email_start_delivery":
                    return SimpleNamespace(data=self.row)
                result = self.finish_results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return SimpleNamespace(data=result)
            return SimpleNamespace(execute=execute)
        self.admin.rpc.side_effect = rpc
        self.send = patch.object(notify, "_send").start()
        patch.object(notify.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def test_success_acknowledgement_retries_without_resending(self):
        self.finish_results = [ConnectionError("response lost"), True]
        self.assertEqual(notify.dispatch_pending(self.admin, self.cfg), {"sent": 1, "failed": 0, "skipped": 0})
        self.send.assert_called_once()
        finishes = [p for name, p in self.calls if name == "email_finish_delivery"]
        self.assertEqual(len(finishes), 2)
        self.assertEqual(finishes[0], finishes[1])
        self.assertTrue(finishes[0]["p_sent"])

    def test_acknowledgement_outage_leaves_lease_for_recovery(self):
        self.finish_results = [ConnectionError("offline")] * 3
        with self.assertRaises(ConnectionError):
            notify.dispatch_pending(self.admin, self.cfg)
        self.send.assert_called_once()
        finishes = [p for name, p in self.calls if name == "email_finish_delivery"]
        self.assertTrue(all(p["p_sent"] for p in finishes))

    def test_expired_claim_makes_no_smtp_call(self):
        self.row = {"started": False}
        self.assertEqual(notify.dispatch_pending(self.admin, self.cfg)["skipped"], 1)
        self.send.assert_not_called()
        self.assertNotIn("email_finish_delivery", [n for n, _ in self.calls])

    def test_inactive_recipient_is_failed_without_sending(self):
        self.row["active"] = False
        self.assertEqual(notify.dispatch_pending(self.admin, self.cfg)["failed"], 1)
        self.send.assert_not_called()
        outcome = [p for n, p in self.calls if n == "email_finish_delivery"][0]
        self.assertFalse(outcome["p_sent"])
        self.assertFalse(outcome["p_ambiguous"])

    def test_ambiguous_smtp_failure_is_recorded(self):
        self.send.side_effect = notify.EmailDeliveryError("response lost", ambiguous=True)
        self.assertEqual(notify.dispatch_pending(self.admin, self.cfg)["failed"], 1)
        outcome = [p for n, p in self.calls if n == "email_finish_delivery"][0]
        self.assertTrue(outcome["p_ambiguous"])

    def test_stale_worker_cannot_claim_success(self):
        self.finish_results = [False]
        with self.assertLogs(notify.log, level="ERROR"):
            self.assertEqual(notify.dispatch_pending(self.admin, self.cfg)["skipped"], 1)

    def test_non_admin_dispatch_endpoint_is_forbidden(self):
        from app import main
        from app.auth import Caller
        for role in ("STUDENT", "DEPARTMENT_STAFF", "DEPARTMENT_ADMIN"):
            caller = Caller("test-id", "staff@example.test", role, [1])
            with self.assertRaises(main.HTTPException) as error:
                main.dispatch_notifications(caller, self.admin, self.cfg)
            self.assertEqual(error.exception.status_code, 403)
        self.admin.rpc.assert_not_called()
