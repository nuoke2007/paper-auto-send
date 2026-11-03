import unittest
from unittest.mock import patch
from datetime import datetime

from paper_auto_send.security import PAPER_CATEGORIES, build_email_html, paper_category, paper_subject
from paper_auto_send.mailer import send_email


class EmailFormatTests(unittest.TestCase):
    @patch.dict("os.environ", {"EMAIL_FROM": "sender@example.com", "EMAIL_TO": "recipient@example.com", "EMAIL_SMTP_PWD": "test-secret"})
    @patch("paper_auto_send.mailer.smtplib.SMTP_SSL")
    def test_daily_summary_labels_resends_and_escapes_source_status(self, smtp):
        server = smtp.return_value.__enter__.return_value
        server.send_message.return_value = {}
        papers = [{"title": "2025一年级语文上册期末卷", "link": "https://www.xxsj.org/sjdetail/1.html"}]
        statuses = {"xxsj.org": "扫描完成，1份", "<img src=x>": "<script>失败</script>"}
        self.assertTrue(send_email(papers, datetime(2026, 11, 3), source_status=statuses))
        message = server.send_message.call_args.args[0]
        self.assertIn("今日四站试卷汇总", message["Subject"])
        body = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("本次汇总 1 份", body)
        self.assertNotIn("本次新增", body)
        self.assertIn("可能包含已推送资源", body)
        self.assertIn("扫描完成", body)
        self.assertNotIn("<script>", body)
        self.assertNotIn("<img", body)

    def test_subjects_and_ambiguous_titles(self):
        examples = {
            "一年级语文上册期末卷": "语文",
            "数学上册单元卷": "数学",
            "英语Unit 1练习": "英语",
            "道德与法治期中卷": "道德与法治",
            "道法上册练习": "道德与法治",
            "品德与生活测试": "道德与法治",
            "科学期末卷": "科学",
            "音乐试卷": "音乐",
            "美术试卷": "美术",
            "语文数学合集": "综合／未明确学科",
            "期末试卷": "综合／未明确学科",
        }
        for title, expected in examples.items():
            with self.subTest(title=title):
                self.assertEqual(paper_subject(title), expected)

    def test_subject_then_category_counts_and_single_assignment(self):
        titles = ["数学期末卷", "语文期中卷", "数学单元卷", "语文期末卷", "英语课堂练习"]
        papers = [{"title": title, "link": f"https://www.xxsj.org/sjdetail/{i}.html"}
                  for i, title in enumerate(titles)]
        body = build_email_html(papers)
        self.assertIn("语文 2份 · 数学 2份 · 英语 1份", body)
        self.assertLess(body.index(">语文 <span"), body.index(">数学 <span"))
        self.assertLess(body.index(">数学 <span"), body.index(">英语 <span"))
        self.assertNotIn(">科学 <span", body)
        for paper in papers:
            self.assertEqual(body.count(f'href="{paper["link"]}"'), 1)
        math_section = body.split(">数学 <span", 1)[1].split("</td></tr>", 1)[0]
        self.assertIn("数学期末卷", math_section)
        self.assertIn("数学单元卷", math_section)
        self.assertNotIn("语文期末卷", math_section)

    @patch.dict("os.environ", {"EMAIL_FROM": "sender@example.com", "EMAIL_TO": "recipient@example.com", "EMAIL_SMTP_PWD": "test-secret"})
    @patch("paper_auto_send.mailer.smtplib.SMTP_SSL")
    def test_preview_subject_is_not_a_new_resource_claim(self, smtp):
        server = smtp.return_value.__enter__.return_value
        server.send_message.return_value = {}
        self.assertTrue(send_email(
            [{"title": "2025一年级上册期末试卷", "link": "https://www.xxsj.org/sjdetail/1.html"}],
            datetime(2026, 11, 3, 10, 0), test_email=True))
        message = server.send_message.call_args.args[0]
        self.assertIn("排版测试", message["Subject"])
        self.assertNotIn("新增", message["Subject"])
        self.assertIn("期末考试卷", message.get_body(preferencelist=("html",)).get_content())

    def test_categories_and_precedence(self):
        examples = {
            "一年级上册期中考试卷": "期中考试卷",
            "一年级上学期期末模拟测试卷": "期末考试卷",
            "一年级上册第六单元检测": "单元检测",
            "Unit 3 test": "单元检测",
            "一年级上册同步课堂练习": "随堂练习与阶段测评",
            "一年级月考阶段测评": "随堂练习与阶段测评",
            "期末专项复习：计算易错题": "专项训练",
            "期末知识点测试卷": "专项训练",
            "期中复习综合练习": "综合复习",
            "2025一年级上册数学试卷": "其他试卷",
        }
        for title, expected in examples.items():
            with self.subTest(title=title):
                self.assertEqual(paper_category(title), expected)

    def test_groups_counts_order_and_each_resource_once(self):
        titles = ["期末测试卷", "第六单元练习", "期中考试卷", "期末测试卷"]
        papers = [
            {"title": title, "link": f"https://www.xxsj.org/sjdetail/{index}.html",
             "date_basis": "title_year", "title_year": "2025"}
            for index, title in enumerate(titles)
        ]
        body = build_email_html(papers)
        self.assertIn("本次新增 4 份", body)
        self.assertIn("期末考试卷 2份", body)
        self.assertLess(body.index("<h2"), body.index("<ol"))
        positions = [body.index(f">{category} <span") for category in PAPER_CATEGORIES[:3]]
        self.assertEqual(positions, sorted(positions))
        for paper in papers:
            self.assertEqual(body.count(f'href="{paper["link"]}"'), 1)
        self.assertNotIn(">其他试卷 <span", body)
        self.assertIn("非发布日期", body)

    def test_metadata_is_escaped_and_unsafe_links_rejected(self):
        paper = {
            "title": '<script>alert("x")</script>期末试卷',
            "link": "https://www.xxsj.org/sjdetail/1.html?a=1&b=2",
            "source": "<img src=x>",
            "region_basis": "<svg onload=x>",
            "date_basis": "title_year",
            "title_year": '<img src="x">',
        }
        body = build_email_html([paper])
        self.assertNotIn("<script>", body)
        self.assertNotIn("<img", body)
        self.assertNotIn("<svg", body)
        self.assertIn("a=1&amp;b=2", body)
        with self.assertRaises(ValueError):
            build_email_html([dict(paper, link="javascript:alert(1)")])

    def test_empty_render_is_valid_but_does_not_invent_resources(self):
        body = build_email_html([])
        self.assertIn("暂无新增试卷", body)
        self.assertNotIn("<li", body)
