"""Notification email content and dispatch; no SMTP server or database needed."""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.update(
    SUPABASE_URL="https://notify-test.supabase.co",
    SUPABASE_PUBLISHABLE_KEY="test-publishable-key",
    SUPABASE_SERVICE_ROLE_KEY="test-service-key",
    SMTP_HOST="smtp-relay.brevo.com",
)

from app import notify
from app.config import Settings


def _cfg() -> Settings:
    return Settings(smtp_host="smtp-relay.brevo.com", smtp_user="u@smtp-brevo.com",
                    smtp_password="k", smtp_from="CampusFix <ok@example.com>")


class NotifyTests(unittest.TestCase):
    def test_send_uses_starttls_login_and_html_alternative(self):
        with patch("app.notify.smtplib.SMTP") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            notify._send(_cfg(), "a@caldwell.edu", "Subj", "plain", "<b>hi</b>")
        smtp_cls.assert_called_once_with("smtp-relay.brevo.com", 587, timeout=15)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("u@smtp-brevo.com", "k")
        sent = smtp.send_message.call_args.args[0]
        self.assertEqual(sent["Subject"], "Subj")
        self.assertEqual(sent.get_body(("html",)).get_content().strip(), "<b>hi</b>")

    def test_html_body_escapes_message(self):
        out = notify._html_body("<script>x</script>", "https://a/b?c=1&d=2")
        self.assertNotIn("<script>", out)
        self.assertIn("&amp;d=2", out)

    def test_dispatch_picks_subject_by_type_and_marks_sent(self):
        row = {"notification_id": 1, "ticket_id": 7, "type": "ASSIGNED", "message": "m",
               "delivery_status": "PENDING", "attempts": 0, "last_attempt_at": None,
               "users": {"email": "a@caldwell.edu", "active": True}}
        admin = MagicMock()
        admin.table.return_value.select.return_value.in_.return_value.lt.return_value \
            .order.return_value.limit.return_value.execute.return_value.data = [row]
        with patch("app.notify._send") as send:
            result = notify.dispatch_pending(admin, _cfg())
        self.assertEqual(result, {"sent": 1, "failed": 0, "skipped": 0})
        self.assertEqual(send.call_args.args[2], notify.SUBJECTS["ASSIGNED"])
        update = admin.table.return_value.update.call_args.args[0]
        self.assertEqual(update["delivery_status"], "SENT")


if __name__ == "__main__":
    unittest.main()
