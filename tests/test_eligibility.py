import unittest
from datetime import date

from paper_auto_send.academic import keywords_for_date
from paper_auto_send.eligibility import eligibility_basis
from paper_auto_send.runner import newly_found
from paper_auto_send.sources import ScanResult, extract_papers, source_definitions


class EligibilityTests(unittest.TestCase):
    def test_either_jiangsu_or_subject_specific_version_is_required(self):
        accepted = (
            "江苏省2026一年级数学上册卷",
            "2025昆山市一年级科学上学期测试卷",
            "2025河南省苏教版一年级数学上册卷",
            "2025山东省统编版一年级语文上册卷",
            "2025人教版统编一年级语文上册卷",
            "2025译林版一年级英语上册卷",
            "2025苏少版一年级音乐上册卷",
            "2025部编版一年级道法上册卷",
        )
        rejected = (
            "2026辽宁沈阳市一年级语文上学期测试卷",
            "2026山东省青岛市一年级语文上册卷",
            "2025人教版一年级数学上册卷",
            "2025苏教版一年级语文上册卷",
            "2025统编版一年级数学上册卷",
            "2025苏少版一年级科学上册卷",
            "2025北师大版苏教版一年级数学上册卷",
            "2025译林三起点一年级英语上册卷",
            "2025译林版三年级英语上册卷",
            "2025期末一年级上册试卷",
        )
        for title in accepted:
            with self.subTest(title=title):
                self.assertIsNotNone(eligibility_basis(title))
        for title in rejected:
            with self.subTest(title=title):
                self.assertIsNone(eligibility_basis(title))

    def test_all_sources_enforce_same_policy_and_store_basis(self):
        today = date(2026, 11, 4)
        for source in source_definitions(keywords_for_date(today)):
            for title, expected in (
                ("2025江苏省一年级数学上册测试卷", 1),
                ("2025苏教版一年级数学上册测试卷", 1),
                ("2025河南省人教版一年级数学上册测试卷", 0),
                ("2025辽宁省一年级语文上册测试卷", 0),
            ):
                page = (
                    '<li class="resource-item"><span class="tag-free">免费</span>'
                    f'<a href="/paper/1">{title}</a></li>'
                )
                with self.subTest(source=source.name, title=title):
                    papers = extract_papers(page, f"https://{source.domains[0]}/",
                                            source, source.keywords, today)
                    self.assertEqual(len(papers), expected)
                    if papers:
                        self.assertIn("eligibility_basis", papers[0])

    def test_final_delivery_rechecks_title_instead_of_trusting_stored_basis(self):
        papers = [
            {"title": title, "link": f"https://www.xxsj.org/sjdetail/{i}.html",
             "date_basis": "title_year", "title_year": "2025",
             "eligibility_basis": "江苏地区标题"}
            for i, title in enumerate((
                "2025江苏省一年级数学上册卷",
                "2025苏教版一年级数学上册卷",
                "2025辽宁省一年级数学上册卷",
                "2025人教版一年级数学上册卷",
            ))
        ]
        fresh = newly_found(ScanResult(papers, [], 4), set(), date(2026, 11, 4))
        self.assertEqual(len(fresh), 2)
        self.assertIn("教材版本：数学", fresh[1]["eligibility_basis"])
