from __future__ import annotations

import tempfile
import unittest
import json
import html
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from paper_auto_send.academic import grade_for_date, keywords_for_date
from paper_auto_send.dates import is_recent, is_recent_title_year, parse_publication_date, three_year_cutoff, title_year
from paper_auto_send.history import load_sent, save_sent
from paper_auto_send.mailer import MailDeliveryError, send_email
from paper_auto_send.runner import newly_found, run
from paper_auto_send.security import build_email_html, safe_resource_url
from paper_auto_send.sources import (
    ScanResult,
    Source,
    SourceAccessError,
    _contains_keyword,
    _has_public_listing,
    _search_requests,
    _scan_source,
    _search_filter_url,
    _verified_result_state,
    extract_papers,
    source_definitions,
)


class AcademicCalendarTests(unittest.TestCase):
    def test_grade_advances_at_september_and_stays_during_summer(self) -> None:
        self.assertEqual(grade_for_date(date(2026, 8, 31)), 1)
        self.assertEqual(grade_for_date(date(2026, 9, 1)), 1)
        self.assertEqual(grade_for_date(date(2027, 8, 31)), 1)
        self.assertEqual(grade_for_date(date(2027, 9, 1)), 2)
        self.assertEqual(grade_for_date(date(2031, 9, 1)), 6)
        self.assertIn("苏教版二年级数学", keywords_for_date(date(2027, 11, 3)))

    def test_invalid_grade_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            from paper_auto_send.academic import keywords_for_grade

            keywords_for_grade(0)


class DateFilterTests(unittest.TestCase):
    def test_title_year_is_conservative_and_rejects_ambiguity(self) -> None:
        today = date(2026, 11, 3)
        for text, expected in (
            ("苏教版2025一年级数学", 2025),
            ("2025-2026学年试卷", 2025),
            ("2025—2026 学年试卷", 2025),
            ("2025至2026学年", 2025),
            ("试卷无年份", None),
            ("2024-2026学年", None),
            ("2024和2025试卷", None),
            ("2024-2025及2026", None),
            ("编号12025001", None),
        ):
            with self.subTest(text=text):
                self.assertEqual(title_year(text), expected)
        self.assertFalse(is_recent_title_year("2023-2024学年", today))
        self.assertTrue(is_recent_title_year("2024-2025学年", today))
        self.assertTrue(is_recent_title_year("2026-2027学年", today))
        self.assertFalse(is_recent_title_year("2027年试卷", today))
        self.assertTrue(is_recent_title_year("2023年试卷", date(2026, 1, 1)))

    def test_cutoff_handles_leap_day_and_includes_exact_boundary(self) -> None:
        today = date(2024, 2, 29)
        self.assertEqual(three_year_cutoff(today), date(2021, 2, 28))
        self.assertTrue(is_recent(date(2021, 2, 28), today))
        self.assertFalse(is_recent(date(2021, 2, 27), today))
        self.assertFalse(is_recent(date(2024, 3, 1), today))

    def test_publication_date_formats_and_missing_values(self) -> None:
        self.assertEqual(parse_publication_date("发布于 2024年2月9日"), date(2024, 2, 9))
        self.assertEqual(parse_publication_date("2024/02/09 10:30"), date(2024, 2, 9))
        self.assertIsNone(parse_publication_date(None))
        self.assertIsNone(parse_publication_date("日期未知"))
        self.assertIsNone(parse_publication_date("2024-13-40"))


class UrlAndHtmlSafetyTests(unittest.TestCase):
    def test_primary_title_year_fallback_and_publication_precedence(self) -> None:
        source = source_definitions(keywords_for_date(date(2026, 11, 3)))[0]
        def page(title, extra=""):
            return f'<li><a href="/html/6901.html">{title}</a>{extra}</li>'
        title = "苏教版2025-2026一年级数学期末测试卷"
        papers = extract_papers(page(title), source.entry_urls[0], source, source.keywords, date(2026, 11, 3))
        self.assertEqual(len(papers), 1)
        self.assertNotIn("published", papers[0])
        self.assertEqual(papers[0]["date_basis"], "title_year")
        self.assertEqual(papers[0]["title_year"], "2025")
        self.assertEqual(len(newly_found(ScanResult(papers, [], 1), set(), date(2026, 11, 3))), 1)
        body = build_email_html(papers)
        self.assertIn("非发布日期", body)
        self.assertIn("标题年份：2025", body)
        for published in ("2020-01-01", "2027-01-01"):
            self.assertEqual(extract_papers(page(title, f'<time>{published}</time>'),
                source.entry_urls[0], source, source.keywords, date(2026, 11, 3)), [])
        dated = extract_papers(page(title, '<time>2026-01-01</time>'),
            source.entry_urls[0], source, source.keywords, date(2026, 11, 3))
        self.assertEqual(dated[0]["published"], "2026-01-01")
        self.assertNotIn("date_basis", dated[0])
        for invalid in ("苏教版一年级数学试卷", "苏教版2023-2024一年级数学试卷"):
            self.assertEqual(extract_papers(page(invalid), source.entry_urls[0], source,
                source.keywords, date(2026, 11, 3)), [])
        strict = Source("牛津中小学英语网", ("njzxxyy.com",), (), ("译林版一年级英语",))
        self.assertEqual(extract_papers(page("译林版2025一年级英语试卷"),
            "https://www.njzxxyy.com/", strict, strict.keywords, date(2026, 11, 3)), [])
        forged = dict(papers[0], link="https://www.21cnjy.com/html/6901.html")
        self.assertEqual(newly_found(ScanResult([forged], [], 3), set(), date(2026, 11, 3)), [])

    def test_only_allowlisted_https_resource_urls_are_accepted(self) -> None:
        domains = ("21cnjy.com",)
        self.assertEqual(
            safe_resource_url("http://xiaoxue.21cnjy.com/a?utm_source=x&id=3#part", domains),
            "https://xiaoxue.21cnjy.com/a?id=3",
        )
        self.assertIsNone(safe_resource_url("javascript:alert(1)", domains))
        self.assertIsNone(safe_resource_url("https://evil.example/a", domains))
        self.assertIsNone(safe_resource_url("https://21cnjy.com.evil.example/a", domains))

    def test_email_html_escapes_title_and_attribute(self) -> None:
        body = build_email_html(
            [{"title": '<img src=x onerror="alert(1)">', "link": "https://appsj.szxuexiao.com/a?x=1&y=2"}]
        )
        self.assertIn("&lt;img", body)
        self.assertIn("&quot;alert(1)&quot;", body)
        self.assertIn("x=1&amp;y=2", body)
        self.assertNotIn("<img", body)
        with self.assertRaises(ValueError):
            build_email_html([{"title": "恶意链接", "link": "javascript:alert(1)"}])

    def test_21cnjy_requires_explicit_free_marker_and_date(self) -> None:
        source = Source("二一教育网", ("21cnjy.com",), (), ("苏教版一年级数学",), True)
        base = "https://xiaoxue.21cnjy.com/list"
        item = (
            '<ul class="resource-list"><li class="resource-item">'
            '<span class="tag-free">免费</span><a href="/detail/1">'
            "苏教版一年级数学测试卷</a><span class=pubtime>2025-03-02</span>"
            "</li></ul>"
        )
        self.assertEqual(len(extract_papers(item, base, source, source.keywords, date(2026, 1, 1))), 1)
        missing_date = item.replace("2025-03-02", "日期未知")
        self.assertEqual(extract_papers(missing_date, base, source, source.keywords, date(2026, 1, 1)), [])
        no_free = item.replace('<span class="tag-free">免费</span>', "")
        self.assertEqual(extract_papers(no_free, base, source, source.keywords, date(2026, 1, 1)), [])

    def test_public_listing_without_publication_date_is_verified_but_skipped(self) -> None:
        source = Source("小学试卷网", ("appsj.szxuexiao.com",), (), ("苏教版一年级数学",))
        page = (
            '<div class="resource-item"><a href="/html/1.html">'
            "苏教版2025-2026一年级数学测试卷</a></div>"
        )
        base = "https://appsj.szxuexiao.com/sujiaoban/index.html"
        self.assertTrue(_has_public_listing(page, base, source, source.keywords))
        self.assertEqual(extract_papers(page, base, source, source.keywords, date(2026, 1, 1)), [])

    def test_publication_date_is_read_from_datetime_attribute(self) -> None:
        source = Source("小学试卷网", ("appsj.szxuexiao.com",), (), ("苏教版一年级数学",))
        page = (
            '<article><a href="/html/1.html">'
            "苏教版2025-2026一年级数学测试卷</a>"
            '<time datetime="2025-11-02"></time></article>'
        )
        papers = extract_papers(
            page,
            "https://appsj.szxuexiao.com/sujiaoban/index.html",
            source,
            source.keywords,
            date(2026, 1, 1),
        )
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["published"], "2025-11-02")

    def test_title_dates_and_reply_dates_are_not_publication_dates(self) -> None:
        source = Source("小学试卷网", ("appsj.szxuexiao.com",), (), ("苏教版一年级数学",))
        page = '<li><a href="/html/1.html">苏教版一年级数学2025-11-02练习</a><span class="reply-time">2025-11-03</span></li>'
        self.assertEqual(extract_papers(page, "https://appsj.szxuexiao.com/", source, source.keywords, date(2026, 1, 1)), [])

    def test_free_cards_use_resource_metadata_not_search_query(self) -> None:
        source = Source("二一教育网", ("21cnjy.com",), (), ("苏教版一年级数学",), True)
        metadata = {
            "title": "一年级数学期末测试卷",
            "version_name": "苏教版",
            "subject_name": "数学",
            "types": {"7": "试卷"},
            "coin": "0",
        }
        def page_for(data: dict, label: str = "免费") -> str:
            encoded = html.escape(json.dumps(data), quote=True)
            return (
                f'<li class="pt-item"><div hmtproperty="{encoded}">'
                '<a href="https://www.21cnjy.com/H/3/45/123.shtml?kw=x&qid=changing&rank=1">'
                '一年级数学期末测试卷</a></div><span class="time">2025-11-03</span>'
                f'<span class="price">{label}</span></li>'
            )
        papers = extract_papers(page_for(metadata), "https://www.21cnjy.com/", source, source.keywords, date(2026, 1, 1))
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["link"], "https://www.21cnjy.com/H/3/45/123.shtml")
        for change in ({"version_name": "人教版"}, {"types": {"3": "教案"}}, {"coin": "10"}):
            self.assertEqual(
                extract_papers(page_for({**metadata, **change}), "https://www.21cnjy.com/", source, source.keywords, date(2026, 1, 1)),
                [],
            )
        self.assertEqual(extract_papers(page_for(metadata, "免费预览"), "https://www.21cnjy.com/", source, source.keywords, date(2026, 1, 1)), [])

    def test_verified_results_cannot_be_inferred_from_keyword_or_network(self) -> None:
        self.assertFalse(_verified_result_state("<input value='苏教版一年级数学'><p>欢迎使用搜索</p>"))
        self.assertFalse(_verified_result_state("<p>访问被拒绝</p>"))
        self.assertTrue(_verified_result_state("<p>共 0 个结果</p>"))
        self.assertTrue(_verified_result_state('<div class="search-result"><li class="pt-item">资源</li></div>'))

    def test_discovered_free_filter_stays_on_source_domain(self) -> None:
        source = Source("二一教育网", ("21cnjy.com",), (), ("苏教版一年级数学",), True)
        self.assertEqual(
            _search_filter_url('<a data-param="flag=1" href="/search/asset?flag=1">免费</a>', "https://www.21cnjy.com/search/asset", source, "免费"),
            "https://www.21cnjy.com/search/asset?flag=1",
        )
        self.assertIsNone(
            _search_filter_url('<a data-param="flag=1" href="https://bad.example">免费</a>', "https://www.21cnjy.com/", source, "免费")
        )

    def test_search_only_uses_same_origin_get_forms(self) -> None:
        source = Source("小学试卷网", ("appsj.szxuexiao.com",), (), ("苏教版一年级数学",))
        page = (
            '<form method="get" action="/search"><input name="keyword" type="text">'
            '<button type="submit">搜索</button></form>'
            '<form method="post" action="/private"><input name="keyword"></form>'
            '<form method="get" action="https://attacker.example/search">'
            '<input name="keyword"></form>'
        )
        searches = _search_requests(page, "https://appsj.szxuexiao.com/", source, source.keywords[0])
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0].method, "GET")
        self.assertIn("keyword=", searches[0].url)
        self.assertIn(
            "%E8%8B%8F%E6%95%99%E7%89%88%E4%B8%80%E5%B9%B4%E7%BA%A7%E6%95%B0%E5%AD%A6",
            searches[0].url,
        )
        self.assertTrue(
            _contains_keyword(
                "苏教版2025-2026一年级数学上学期测试卷",
                "苏教版一年级数学",
            )
        )
        english_source = next(
            source
            for source in source_definitions(keywords_for_date(date(2026, 11, 3)))
            if source.name == "牛津中小学英语网"
        )
        self.assertIn("译林版一年级英语", english_source.keywords)
        self.assertIn("三起点", english_source.exclude_terms)

    def test_discuz_search_post_is_limited_to_public_search_form(self) -> None:
        source = Source("牛津中小学英语网", ("njzxxyy.com",), (), ("译林一起点一年级英语",))
        page = (
            '<form method="post" action="search.php?searchsubmit=yes">'
            '<input type="hidden" name="formhash" value="public-form-token">'
            '<input type="hidden" name="mod" value="forum">'
            '<input type="text" name="srchtxt"><button type="submit">搜索</button></form>'
            '<form method="post" action="member.php?mod=logging"><input name="username"></form>'
        )
        searches = _search_requests(
            page, "https://www.njzxxyy.com/", source, source.keywords[0]
        )
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0].method, "POST")
        self.assertIn(("srchtxt", source.keywords[0]), searches[0].params)
        self.assertIn(("formhash", "public-form-token"), searches[0].params)

    def test_unrecognized_site_page_is_not_reported_as_verified(self) -> None:
        source = Source(
            "小学试卷网",
            ("appsj.szxuexiao.com",),
            ("https://appsj.szxuexiao.com/",),
            ("苏教版一年级数学",),
        )
        fetcher = Mock()
        fetcher.get.return_value = ("<html><body>站点维护中</body></html>", source.entry_urls[0])
        papers, verified = _scan_source(source, date(2026, 11, 3), fetcher)
        self.assertEqual(papers, [])
        self.assertFalse(verified)

    def test_denied_source_stops_without_trying_another_route(self) -> None:
        source = Source(
            "小学试卷网", ("appsj.szxuexiao.com",),
            ("https://appsj.szxuexiao.com/", "https://appsj.szxuexiao.com/sujiaoban/index.html"),
            ("苏教版一年级数学",),
        )
        fetcher = Mock()
        fetcher.get.side_effect = SourceAccessError("HTTP status 403")
        papers, verified = _scan_source(source, date(2026, 11, 3), fetcher)
        self.assertEqual(papers, [])
        self.assertFalse(verified)
        self.assertEqual(fetcher.get.call_count, 1)


class HistoryAndDeliveryTests(unittest.TestCase):
    @patch("paper_auto_send.runner.save_sent")
    @patch("paper_auto_send.runner.send_email")
    @patch("paper_auto_send.runner.load_sent", return_value=set())
    @patch("paper_auto_send.runner.crawl_all")
    def test_primary_only_run_validates_and_saves_title_year_items(self, crawl, load, send, save) -> None:
        paper = {"title": "苏教版2025一年级数学试卷",
                 "link": "https://appsj.szxuexiao.com/html/1.html",
                 "date_basis": "title_year", "title_year": "2025"}
        crawl.return_value = ScanResult([paper], [], 1, {"小学试卷网": 1}, 1)
        self.assertEqual(run(source_filter="primary"), 0)
        self.assertEqual(crawl.call_args.args[1], "primary")
        send.assert_called_once()
        save.assert_called_once()

    def test_history_is_persisted_atomically_as_a_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sent_list.json"
            save_sent({"https://example.test/a"}, path)
            self.assertEqual(load_sent(path), {"https://example.test/a"})
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    @patch("paper_auto_send.mailer.smtplib.SMTP_SSL")
    def test_no_new_resources_never_contacts_smtp(self, smtp) -> None:
        self.assertFalse(send_email([]))
        smtp.assert_not_called()

    @patch.dict("os.environ", {"EMAIL_FROM": "sender@example.com", "EMAIL_TO": "recipient@example.com", "EMAIL_SMTP_PWD": "test-secret"})
    @patch("paper_auto_send.mailer.smtplib.SMTP_SSL")
    def test_successful_delivery_uses_fixed_recipient(self, smtp_factory) -> None:
        server = smtp_factory.return_value.__enter__.return_value
        server.send_message.return_value = {}
        papers = [{"title": "练习卷", "link": "https://appsj.szxuexiao.com/paper/1"}]
        self.assertTrue(send_email(papers, datetime(2026, 11, 3, 10, 0)))
        args, kwargs = server.send_message.call_args
        message = args[0]
        self.assertEqual(kwargs["to_addrs"], ["recipient@example.com"])
        self.assertEqual(message["To"], "recipient@example.com")
        self.assertEqual(message.get_content_type(), "multipart/alternative")

    @patch.dict("os.environ", {"EMAIL_FROM": "sender@example.com", "EMAIL_TO": "recipient@example.com", "EMAIL_SMTP_PWD": "test-secret"})
    @patch("paper_auto_send.mailer.smtplib.SMTP_SSL")
    def test_delivery_failure_is_explicit(self, smtp_factory) -> None:
        smtp_factory.return_value.__enter__.return_value.send_message.side_effect = OSError("offline")
        with self.assertRaises(MailDeliveryError):
            send_email([{"title": "卷", "link": "https://appsj.szxuexiao.com/a"}])

    def test_duplicate_links_are_not_selected_for_delivery(self) -> None:
        result = ScanResult(
            [
                {
                    "title": "练习卷",
                    "link": "https://appsj.szxuexiao.com/paper?id=1&utm_source=feed",
                    "published": "2025-01-01",
                }
            ],
            [],
            3,
        )
        self.assertEqual(newly_found(result, {"https://appsj.szxuexiao.com/paper?id=1"}), [])

    @patch("paper_auto_send.runner.save_sent")
    @patch("paper_auto_send.runner.send_email")
    @patch("paper_auto_send.runner.load_sent", return_value=set())
    @patch("paper_auto_send.runner.crawl_all", return_value=ScanResult([], [], 3))
    def test_run_with_no_additions_does_not_send_or_save(self, crawl, load, send, save) -> None:
        self.assertEqual(run(), 0)
        send.assert_not_called()
        save.assert_not_called()

    @patch("paper_auto_send.runner.save_sent")
    @patch("paper_auto_send.runner.send_email")
    @patch("paper_auto_send.runner.load_sent", return_value=set())
    @patch(
        "paper_auto_send.runner.crawl_all",
        return_value=ScanResult(
            [{"title": "练习卷", "link": "https://appsj.szxuexiao.com/paper/1", "published": "2025-01-01"}],
            [],
            3,
        ),
    )
    def test_history_is_saved_only_after_delivery_returns(self, crawl, load, send, save) -> None:
        events = []
        send.side_effect = lambda papers: events.append("sent")
        save.side_effect = lambda sent, path: events.append("saved")
        self.assertEqual(run(), 0)
        self.assertEqual(events, ["sent", "saved"])

    @patch("paper_auto_send.runner.save_sent")
    @patch("paper_auto_send.runner.send_email", side_effect=MailDeliveryError("offline"))
    @patch("paper_auto_send.runner.load_sent", return_value=set())
    @patch(
        "paper_auto_send.runner.crawl_all",
        return_value=ScanResult(
            [{"title": "练习卷", "link": "https://appsj.szxuexiao.com/paper/1", "published": "2025-01-01"}],
            [],
            3,
        ),
    )
    def test_history_is_not_saved_after_delivery_failure(self, crawl, load, send, save) -> None:
        with self.assertRaises(MailDeliveryError):
            run()
        save.assert_not_called()

    @patch("paper_auto_send.runner.save_sent")
    @patch("paper_auto_send.runner.send_email")
    @patch("paper_auto_send.runner.load_sent", return_value=set())
    @patch("paper_auto_send.runner.crawl_all")
    def test_dry_run_reports_but_never_delivers_or_changes_history(self, crawl, load, send, save) -> None:
        crawl.return_value = ScanResult(
            [{"title": "练习卷", "link": "https://appsj.szxuexiao.com/html/1.html", "published": "2025-01-01"}],
            [], 3,
        )
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "summary.json"
            self.assertEqual(run(dry_run=True, report_path=report_path), 0)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["dry_run"])
            self.assertEqual(report["new_resources"], 1)
            self.assertFalse(report["delivered"])
        send.assert_not_called()
        save.assert_not_called()

    @patch("paper_auto_send.runner.save_sent")
    @patch("paper_auto_send.runner.send_email")
    @patch("paper_auto_send.runner.load_sent", return_value=set())
    @patch("paper_auto_send.runner.crawl_all", return_value=ScanResult([], ["source"], 2))
    def test_partial_crawl_returns_failure_even_in_dry_run(self, crawl, load, send, save) -> None:
        self.assertEqual(run(dry_run=True), 1)
        send.assert_not_called()
        save.assert_not_called()

    @patch.dict("os.environ", {"EMAIL_FROM": "sender@example.com", "EMAIL_TO": "", "EMAIL_SMTP_PWD": "test-secret"}, clear=True)
    @patch("paper_auto_send.mailer.smtplib.SMTP_SSL")
    def test_missing_recipient_blocks_delivery_without_contacting_smtp(self, smtp_factory) -> None:
        with self.assertRaises(MailDeliveryError):
            send_email([{"title": "卷", "link": "https://appsj.szxuexiao.com/html/1.html"}])
        smtp_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
