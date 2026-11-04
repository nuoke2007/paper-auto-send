import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from paper_auto_send.history import load_catalog, save_catalog
from paper_auto_send.mailer import MailDeliveryError, send_email
from paper_auto_send.runner import run
from paper_auto_send.security import build_email_html
from paper_auto_send.sources import ScanResult


def paper(number, title="苏教版2025一年级数学上册期末卷"):
    return {
        "title": title, "link": f"https://www.xxsj.org/sjdetail/{number}.html",
        "source": "xxsj.org", "date_basis": "title_year", "title_year": "2025",
    }


class DailyDigestTests(unittest.TestCase):
    def setUp(self):
        patches = {
            "clock": patch("paper_auto_send.runner.datetime"),
            "load": patch("paper_auto_send.runner.load_catalog", return_value=[]),
            "save": patch("paper_auto_send.runner.save_catalog"),
            "sent": patch("paper_auto_send.runner.load_sent", return_value=set()),
            "record": patch("paper_auto_send.runner.save_sent"),
            "crawl": patch("paper_auto_send.runner.crawl_all"),
            "email": patch("paper_auto_send.runner.send_email", return_value=True),
        }
        for name, mocked in patches.items():
            self.addCleanup(mocked.stop)
            setattr(self, name, mocked.start())
        self.clock.now.return_value = datetime(2026, 11, 4)

    def test_history_retained_new_resources_marked_and_deduplicated(self):
        old, new = paper(1), paper(2)
        self.load.return_value = [old]
        self.sent.return_value = {old["link"]}
        self.crawl.return_value = ScanResult([new, new], [], 1, {"xxsj.org": 1}, 1)
        self.assertEqual(run(), 0)
        args, kwargs = self.email.call_args
        self.assertEqual({item["link"] for item in args[0]}, {old["link"], new["link"]})
        self.assertEqual(kwargs["new_links"], {new["link"]})
        self.assertEqual(len(self.save.call_args.args[0]), 2)
        self.assertEqual(self.record.call_args.args[0], {old["link"], new["link"]})

    def test_no_additions_sends_all_old_resources(self):
        old = paper(1)
        self.load.return_value = [old]
        self.sent.return_value = {old["link"]}
        self.crawl.return_value = ScanResult([], [], 1, {"xxsj.org": 0}, 1)
        self.assertEqual(run(), 0)
        self.assertEqual(len(self.email.call_args.args[0]), 1)
        self.assertEqual(self.email.call_args.kwargs["new_links"], set())
        self.record.assert_not_called()

    def test_total_site_failure_still_sends_archived_current_resources_with_warning(self):
        self.load.return_value = [paper(1)]
        self.sent.return_value = {paper(1)["link"]}
        self.crawl.return_value = ScanResult([], ["xxsj.org"], 0, {"xxsj.org": 0}, 1)
        self.assertEqual(run(), 1)
        self.assertEqual(len(self.email.call_args.args[0]), 1)
        self.assertIn("扫描未完成", self.email.call_args.kwargs["source_status"]["xxsj.org"])

    def test_revalidates_archive_and_preserves_it_for_future_terms(self):
        stored = [paper(1), paper(2, "苏教版2025一年级数学下册卷"),
                  paper(3, "人教版2025一年级数学上册卷"),
                  dict(paper(4), title="苏教版2020一年级数学上册卷", title_year="2020")]
        self.load.return_value = stored
        self.crawl.return_value = ScanResult([], [], 1, {"xxsj.org": 0}, 1)
        run()
        self.assertEqual([p["link"] for p in self.email.call_args.args[0]], [stored[0]["link"]])
        self.assertEqual(len(self.save.call_args.args[0]), 4)

    def test_smtp_failure_does_not_mark_new_items_sent(self):
        self.crawl.return_value = ScanResult([paper(1)], [], 1, {"xxsj.org": 1}, 1)
        self.email.side_effect = MailDeliveryError("offline")
        with self.assertRaises(MailDeliveryError):
            run()
        self.record.assert_not_called()
        self.save.assert_called_once()

    def test_false_delivery_result_is_not_recorded_as_success(self):
        self.crawl.return_value = ScanResult([paper(1)], [], 1, {"xxsj.org": 1}, 1)
        self.email.return_value = False
        with self.assertRaises(MailDeliveryError):
            run()
        self.record.assert_not_called()

    def test_catalog_failure_prevents_sending_inconsistent_digest(self):
        self.crawl.return_value = ScanResult([paper(1)], [], 1, {"xxsj.org": 1}, 1)
        self.save.side_effect = RuntimeError("disk unavailable")
        with self.assertRaises(RuntimeError):
            run()
        self.email.assert_not_called()
        self.record.assert_not_called()

    def test_dry_run_never_sends_or_writes_catalog(self):
        self.load.return_value = [paper(1)]
        self.crawl.return_value = ScanResult([], [], 1, {"xxsj.org": 0}, 1)
        self.assertEqual(run(dry_run=True), 0)
        self.email.assert_not_called()
        self.save.assert_not_called()
        self.record.assert_not_called()

    def test_empty_daily_mail_is_sent_and_contains_no_new_notice(self):
        self.crawl.return_value = ScanResult([], [], 1, {"xxsj.org": 0}, 1)
        run()
        self.email.assert_called_once_with([], source_status={"xxsj.org": "扫描完成，本次获取0份合格资源"}, new_links=set())

    def test_catalog_roundtrip_corruption_and_atomic_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            self.assertEqual(load_catalog(path), [])
            save_catalog([paper(1)], path)
            self.assertEqual(load_catalog(path), [paper(1)])
            self.assertFalse(path.with_name("catalog.json.tmp").exists())
            for value in ("invalid json", json.dumps(["not an object"])):
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    load_catalog(path)

    def test_html_lists_exact_additions_and_labels_history(self):
        old, new = paper(1), paper(2, "苏教版2025一年级数学上册单元卷")
        body = build_email_html([old, new], new_links={new["link"]})
        self.assertIn("今日新增 1 份", body)
        self.assertIn("【今日新增】", body)
        self.assertIn("【历史保留】", body)
        self.assertEqual(body.count(f'href="{new["link"]}"'), 2)
        self.assertEqual(body.count(f'href="{old["link"]}"'), 1)
        self.assertIn("今日新增 0 份", build_email_html([old], new_links=set()))

    @patch.dict("os.environ", {"EMAIL_FROM": "sender@example.com", "EMAIL_TO": "recipient@example.com", "EMAIL_SMTP_PWD": "test-secret"})
    @patch("paper_auto_send.mailer.smtplib.SMTP_SSL")
    def test_actual_mailer_sends_even_with_no_catalog_resources(self, smtp):
        smtp.return_value.__enter__.return_value.send_message.return_value = {}
        self.assertTrue(send_email([], datetime(2026, 11, 4), new_links=set()))
        message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertIn("今日新增0份", message["Subject"])
        self.assertIn("今日新增 0 份", message.get_body(preferencelist=("html",)).get_content())
