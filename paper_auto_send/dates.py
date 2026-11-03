from __future__ import annotations

import calendar
import re
from datetime import date

_DATE_PATTERN = re.compile(
    r"(?<!\d)(\d{4})\s*(?:年|[-/.])\s*(\d{1,2})\s*(?:月|[-/.])\s*"
    r"(\d{1,2})\s*(?:日|号)?(?!\d)"
)


def parse_publication_date(value: str | None) -> date | None:
    if not value:
        return None
    match = _DATE_PATTERN.search(value)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def three_year_cutoff(today: date) -> date:
    year = today.year - 3
    day = min(today.day, calendar.monthrange(year, today.month)[1])
    return date(year, today.month, day)


def is_recent(publication_date: date | None, today: date) -> bool:
    return (
        publication_date is not None
        and three_year_cutoff(today) <= publication_date <= today
    )


def title_year(title: str) -> int | None:
    years = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", title)
    if len(years) == 1:
        return int(years[0])
    if len(years) == 2:
        start, end = map(int, years)
        if end == start + 1 and re.search(
            rf"{start}\s*[-—–~～至/]\s*{end}(?:\s*学年)?", title
        ):
            return start
    return None


def is_recent_title_year(title: str, today: date) -> bool:
    year = title_year(title)
    # The earliest possible date must fall inside the window; do not invent a publication date.
    return year is not None and is_recent(date(year, 1, 1), today)
