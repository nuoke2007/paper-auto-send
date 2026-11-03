from __future__ import annotations

import calendar
import re
from datetime import date, datetime

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
