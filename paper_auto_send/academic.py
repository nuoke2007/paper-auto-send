from __future__ import annotations

from datetime import date
import re

SUBJECTS = (
    ("部编版", "语文"),
    ("苏教版", "数学"),
    ("译林一起点", "英语"),
    ("部编", "道德与法治"),
    ("苏教版", "科学"),
    ("苏少版", "音乐美术"),
)
FIRST_GRADE_START = date(2026, 9, 1)
CHINESE_GRADES = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")


def grade_for_date(today: date) -> int:
    """Return the grade for the school year in progress (summer stays in grade)."""
    if today < FIRST_GRADE_START:
        return 1
    school_year_start = today.year if today.month >= 9 else today.year - 1
    return 1 + school_year_start - FIRST_GRADE_START.year


def keywords_for_grade(grade: int) -> tuple[str, ...]:
    if grade < 1:
        raise ValueError("grade must be at least 1")
    grade_label = CHINESE_GRADES[grade - 1] if grade <= len(CHINESE_GRADES) else str(grade)
    return tuple(f"{version}{grade_label}年级{subject}" for version, subject in SUBJECTS)


def keywords_for_date(today: date) -> tuple[str, ...]:
    return keywords_for_grade(grade_for_date(today))


def semester_for_date(today: date) -> str:
    return "上" if today.month >= 9 or today.month == 1 else "下"


def matches_current_term(title: str, today: date) -> bool:
    compact = re.sub(r"\s+", "", title)
    grade = grade_for_date(today)
    label = CHINESE_GRADES[grade - 1] if grade <= len(CHINESE_GRADES) else str(grade)
    grades = re.findall(r"(?<![一二三四五六七八九十])([一二三四五六七八九十]+|\d+)年级", compact)
    if not grades or any(value not in (label, str(grade)) for value in grades):
        return False
    if re.search(r"(?:上[、/及和与]?下|下[、/及和与]?上)(?:册|学期)", compact):
        return False
    terms = set(re.findall(r"([上下])(?:册|学期|半学期)", compact))
    for number in re.findall(r"第([一二12])学期", compact):
        terms.add("上" if number in ("一", "1") else "下")
    return terms == {semester_for_date(today)}
