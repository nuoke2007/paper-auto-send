import unittest

from paper_auto_send.security import PAPER_CATEGORIES, build_email_html, paper_category


class EmailFormatTests(unittest.TestCase):
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
