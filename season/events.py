"""Static festival and release calendar, hand-copied from the official wiki.

Each festival maps year -> (start, end) of its actual in-game run, inclusive.
Years a festival skipped are simply absent. Dates checked against the wiki
release tables on 2026-08-07:

- https://wiki.guildwars2.com/wiki/Wintersday
- https://wiki.guildwars2.com/wiki/Halloween (Shadow of the Mad King)
- https://wiki.guildwars2.com/wiki/Super_Adventure_Festival
- https://wiki.guildwars2.com/wiki/Lunar_New_Year
- https://wiki.guildwars2.com/wiki/Dragon_Bash
- https://wiki.guildwars2.com/wiki/Festival_of_the_Four_Winds
- https://wiki.guildwars2.com/wiki/Expansion

Day-of-year spans here use 1..365 (Feb 29 folds into its neighbor); at the
weekly resolution of the seasonal profiles that one-day drift is noise.
"""

from datetime import date
from statistics import median

EVENTS = {
    "Wintersday": {
        2012: ("2012-12-14", "2013-01-03"),
        2013: ("2013-12-10", "2014-01-21"),
        2014: ("2014-12-16", "2015-01-13"),
        2015: ("2015-12-15", "2016-01-12"),
        2016: ("2016-12-13", "2017-01-10"),
        2017: ("2017-12-12", "2018-01-02"),
        2018: ("2018-12-11", "2019-01-02"),
        2019: ("2019-12-17", "2020-01-07"),
        2020: ("2020-12-15", "2021-01-05"),
        2021: ("2021-12-14", "2022-01-04"),
        2022: ("2022-12-13", "2023-01-03"),
        2023: ("2023-12-12", "2024-01-02"),
        2024: ("2024-12-10", "2025-01-02"),
        2025: ("2025-12-09", "2026-01-06"),
    },
    "Halloween": {
        2012: ("2012-10-22", "2012-11-01"),
        2013: ("2013-10-15", "2013-11-11"),
        2014: ("2014-10-21", "2014-11-04"),
        2015: ("2015-10-23", "2015-11-06"),
        2016: ("2016-10-18", "2016-11-08"),
        2017: ("2017-10-17", "2017-11-07"),
        2018: ("2018-10-16", "2018-11-06"),
        2019: ("2019-10-15", "2019-11-05"),
        2020: ("2020-10-13", "2020-11-03"),
        2021: ("2021-10-05", "2021-11-09"),
        2022: ("2022-10-18", "2022-11-08"),
        2023: ("2023-10-17", "2023-11-07"),
        2024: ("2024-10-15", "2024-11-05"),
        2025: ("2025-10-07", "2025-11-04"),
    },
    # 2013 ran twice (April, plus Back to School in September); only the
    # April run is listed since the festival settled on a spring cadence.
    "Super Adventure Festival": {
        2013: ("2013-04-01", "2013-04-30"),
        2016: ("2016-03-31", "2016-04-19"),
        2017: ("2017-03-30", "2017-04-20"),
        2018: ("2018-03-29", "2018-04-19"),
        2019: ("2019-03-28", "2019-04-18"),
        2020: ("2020-04-14", "2020-05-12"),
        2021: ("2021-04-06", "2021-04-27"),
        2022: ("2022-03-29", "2022-04-19"),
        2023: ("2023-03-28", "2023-04-18"),
        2024: ("2024-04-16", "2024-05-07"),
        2025: ("2025-04-15", "2025-05-06"),
        2026: ("2026-04-14", "2026-05-05"),
    },
    "Lunar New Year": {
        2015: ("2015-02-15", "2015-02-24"),
        2016: ("2016-01-26", "2016-02-19"),
        2017: ("2017-01-17", "2017-02-08"),
        2018: ("2018-02-06", "2018-02-22"),
        2019: ("2019-01-22", "2019-02-12"),
        2020: ("2020-01-14", "2020-02-04"),
        2021: ("2021-02-02", "2021-02-23"),
        2022: ("2022-01-25", "2022-02-15"),
        2023: ("2023-01-10", "2023-01-31"),
        2024: ("2024-01-30", "2024-02-20"),
        2025: ("2025-01-28", "2025-02-18"),
        2026: ("2026-02-03", "2026-02-24"),
    },
    "Dragon Bash": {
        2013: ("2013-06-11", "2013-07-09"),
        2019: ("2019-06-25", "2019-07-16"),
        2020: ("2020-06-23", "2020-07-14"),
        2021: ("2021-06-22", "2021-07-13"),
        2022: ("2022-06-07", "2022-06-28"),
        2023: ("2023-06-06", "2023-06-27"),
        2024: ("2024-06-04", "2024-06-25"),
        2025: ("2025-06-17", "2025-07-08"),
        2026: ("2026-06-02", "2026-06-23"),
    },
    # 2013 is the predecessor Bazaar of the Four Winds run.
    "Festival of the Four Winds": {
        2013: ("2013-07-09", "2013-07-23"),
        2014: ("2014-05-20", "2014-07-01"),
        2018: ("2018-07-24", "2018-08-14"),
        2019: ("2019-07-30", "2019-08-20"),
        2020: ("2020-08-11", "2020-09-01"),
        2021: ("2021-08-31", "2021-09-21"),
        2022: ("2022-08-02", "2022-08-23"),
        2023: ("2023-07-18", "2023-08-08"),
        2024: ("2024-07-30", "2024-08-20"),
        2025: ("2025-08-05", "2025-08-26"),
        2026: ("2026-08-11", "2026-09-01"),
    },
}

# Expansion launches reprice whole material classes; cycles that straddle one
# get labeled so a single repriced year is visible in the evidence.
RELEASES = [
    ("2015-10-23", "Heart of Thorns"),
    ("2017-09-22", "Path of Fire"),
    ("2022-02-28", "End of Dragons"),
    ("2023-08-22", "Secrets of the Obscure"),
    ("2024-08-20", "Janthir Wilds"),
    ("2025-10-28", "Visions of Eternity"),
]

MIN_OVERLAP_DAYS = 5  # a glancing 1-day overlap does not make an event trade


def _doy(d):
    return min(d.timetuple().tm_yday, 365)


def _span_days(span):
    """The set of day-of-year values a (start, end) span covers; wrap-aware."""
    a, b = span
    if a <= b:
        return set(range(a, b + 1))
    return set(range(a, 366)) | set(range(1, b + 1))


def typical_span(event):
    """Median day-of-year window across all years the event ran; may wrap."""
    runs = EVENTS[event]
    starts, lengths = [], []
    for a_iso, b_iso in runs.values():
        a, b = date.fromisoformat(a_iso), date.fromisoformat(b_iso)
        starts.append(_doy(a))
        lengths.append((b - a).days)
    start = int(median(starts))
    end = (start - 1 + int(median(lengths))) % 365 + 1
    return start, end


def year_span(event, year):
    """The actual (start, end) dates for one year, or None if it skipped."""
    run = EVENTS.get(event, {}).get(year)
    if not run:
        return None
    return date.fromisoformat(run[0]), date.fromisoformat(run[1])


def overlapping_event(span, min_days=MIN_OVERLAP_DAYS):
    """The event whose typical window overlaps the span most, or None."""
    days = _span_days(span)
    best, best_n = None, min_days - 1
    for name in EVENTS:
        n = len(_span_days(typical_span(name)) & days)
        if n > best_n:
            best, best_n = name, n
    return best


def releases_between(d0, d1):
    """Expansion labels whose launch date falls inside [d0, d1]."""
    return [
        label
        for iso, label in RELEASES
        if d0 <= date.fromisoformat(iso) <= d1
    ]
