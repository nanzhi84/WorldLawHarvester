"""Explicit Japanese era dates used by tax decision and guidance metadata."""

from __future__ import annotations

import re
from datetime import date

from wlh.core import WlhError

_ERAS = {
    "昭": (1925, date(1926, 12, 25), date(1989, 1, 7)),
    "平": (1988, date(1989, 1, 8), date(2019, 4, 30)),
    "令": (2018, date(2019, 5, 1), date.max),
}
_DATE = re.compile(
    r"(昭和|平成|令和|昭|平|令)\s*(元|\d{1,3})\s*"
    r"(?:年|[./-])\s*(\d{1,2})\s*(?:月|[./-])\s*(\d{1,2})\s*日?"
)


def era_date(value: str) -> str | None:
    match = _DATE.search(value)
    if match is None:
        return None
    era, year, month, day = match.groups()
    offset, start, end = _ERAS[era[0]]
    try:
        parsed = date(offset + (1 if year == "元" else int(year)), int(month), int(day))
        if not start <= parsed <= end:
            raise ValueError
        return parsed.isoformat()
    except ValueError:
        raise WlhError("document_date_unparseable") from None
