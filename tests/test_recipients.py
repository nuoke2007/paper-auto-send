import unittest
from unittest.mock import patch

from paper_auto_send.mailer import MailDeliveryError, send_email


@patch.dict("os.environ", {
    "EMAIL_FROM": "sender@example.com",
    "EMAIL_TO": "original@example.com",
    "EMAIL_TO_ADDITIONAL": "new@example.com",
    "EMAIL_SMTP_PWD": "test-secret",
}, clear=True)
class RecipientTests(unittest.TestCase):
    @patch("paper_auto_send.mailer.smtplib.SMTP_SSL")
    def test_original_and_additional_receive_same_digest(self, smtp):
        server = smtp.return_value.__enter__.return_value
        server.send_message.return_value = {}
        self.assertTrue(send_email([], new_links=set()))
        args, kwargs = server.send_message.call_args
        self.assertEqual(kwargs["to_addrs"], ["original@example.com", "new@example.com"])
        self.assertEqual(str(args[0]["To"]), "original@example.com, new@example.com")

    @patch.dict("os.environ", {"EMAIL_TO_ADDITIONAL": "original@example.com, new@example.com, NEW@example.com"})
    @patch("paper_auto_send.mailer.smtplib.SMTP_SSL")
    def test_duplicate_addresses_are_only_sent_once(self, smtp):
        smtp.return_value.__enter__.return_value.send_message.return_value = {}
        send_email([], new_links=set())
        self.assertEqual(
            smtp.return_value.__enter__.return_value.send_message.call_args.kwargs["to_addrs"],
            ["original@example.com", "new@example.com"],
        )

    @patch("paper_auto_send.mailer.smtplib.SMTP_SSL")
    def test_invalid_additional_address_prevents_smtp(self, smtp):
        for value in ("bad", "new@example.com,", "Name <new@example.com>",
                      "new@example.com\r\nBcc: other@example.com"):
            with self.subTest(value=value), patch.dict("os.environ", {"EMAIL_TO_ADDITIONAL": value}):
                with self.assertRaises(MailDeliveryError):
                    send_email([], new_links=set())
        smtp.assert_not_called()

    @patch("paper_auto_send.mailer.smtplib.SMTP_SSL")
    def test_partial_refusal_is_an_explicit_failure(self, smtp):
        smtp.return_value.__enter__.return_value.send_message.return_value = {
            "new@example.com": (550, b"refused")
        }
        with self.assertRaisesRegex(MailDeliveryError, "partial delivery"):
            send_email([], new_links=set())
