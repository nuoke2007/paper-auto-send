from __future__ import annotations

from datetime import date

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
