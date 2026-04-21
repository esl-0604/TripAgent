import re
from datetime import date
from dataclasses import dataclass

# Match: "YYMMDD-DD, 국가, 이벤트명 YYYY..."
# Allows trailing extras (e.g., "CMEF 2026, CACA 2026")
_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})-(\d{2}),\s*([^,]+?),\s*(.+)$")


@dataclass(frozen=True)
class TripInfo:
    title: str
    start_date: date
    end_date: date
    country: str
    event: str


def parse_parent(title: str) -> TripInfo:
    """Parse parent message text into structured trip info.

    Raises ValueError if title doesn't match expected format.
    Assumes YY is 2000s (20YY) and start/end dates are in the same month.
    """
    t = title.strip()
    m = _RE.match(t)
    if not m:
        raise ValueError(f"Cannot parse trip title: {t!r}")
    yy, mm, dd_start, dd_end, country, event = m.groups()
    year = 2000 + int(yy)
    start = date(year, int(mm), int(dd_start))
    end = date(year, int(mm), int(dd_end))
    return TripInfo(
        title=t,
        start_date=start,
        end_date=end,
        country=country.strip(),
        event=event.strip(),
    )


def day_ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 3 -> '3rd', 4+ -> '4th', 11/12/13 -> 'th'."""
    if 11 <= n % 100 <= 13:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def day_folder_name(day_date: date, day_num: int) -> str:
    """Format: '260501-1st Day'"""
    return f"{day_date.strftime('%y%m%d')}-{day_ordinal(day_num)} Day"
