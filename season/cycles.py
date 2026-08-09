"""Per-cycle realized returns for a seasonal buy/sell window pair.

A daily price series is a plain dict {"YYYY-MM-DD": price}. Each past year
is one cycle: buy at the median observed price inside that year's buy
window, sell at the median inside the sell window, net of the 15% fees.
Cycles missing observed prices in either window are dropped, not guessed;
that one gate handles the sparse 2014-15 mirror era and items younger than
the window ("n cycles" then shrinks honestly).
"""

from datetime import date, timedelta
from statistics import median

from season import events

TAX = 0.15  # trading post cut; kept in sync with scorers.flip.TAX by a test
MIN_WINDOW_OBS = 3  # observed days required inside a window, else no cycle
RELEASE_MARGIN_DAYS = 30  # expansions just outside the cycle still reprice it


def daily_price(avg, lo, hi):
    """Daily price with the pre-2019 fallback: rows from before ~2019 carry
    only the min/max extremes (see collector/dw2.py), so use their midpoint
    when the average is null."""
    if avg is not None:
        return avg
    if lo is not None and hi is not None:
        return (lo + hi) / 2
    return lo if lo is not None else hi


def window_dates(year, span_doy):
    """Concrete dates for a day-of-year span; wrapping spans end in year+1."""
    a, b = span_doy
    start = date(year, 1, 1) + timedelta(days=a - 1)
    end = date(year + 1 if b < a else year, 1, 1) + timedelta(days=b - 1)
    return start, end


def anchor_dates(span_doy, anchor_doy, anchor_date):
    """Shift a day-of-year span to follow an event's actual date that year:
    the span keeps its offset from the typical event start."""
    a, b = span_doy
    offset = (a - anchor_doy + 182) % 365 - 182
    length = (b - a) % 365
    start = anchor_date + timedelta(days=offset)
    return start, start + timedelta(days=length)


def cycle_windows(years, buy_span_doy, sell_span_doy, event_name=None):
    """Per-year (buy_start, buy_end, sell_start, sell_end) window dates.

    Event-linked windows track each year's actual festival dates and skip
    years the festival did not run; plain windows sit at fixed day-of-year
    spans. The sell window always follows the buy window in cycle order.
    """
    wins = {}
    anchor = events.typical_span(event_name)[0] if event_name else None
    for year in years:
        if event_name:
            run = events.year_span(event_name, year)
            if run is None:
                continue
            b0, b1 = anchor_dates(buy_span_doy, anchor, run[0])
            s0, s1 = anchor_dates(sell_span_doy, anchor, run[0])
            if s0 < b0:
                s0 += timedelta(days=365)
                s1 += timedelta(days=365)
        else:
            b0, b1 = window_dates(year, buy_span_doy)
            s0, s1 = window_dates(year, sell_span_doy)
            if s0 < b0:
                s0, s1 = window_dates(year + 1, sell_span_doy)
        wins[year] = (b0, b1, s0, s1)
    return wins


def window_price(series, d0, d1):
    """Median observed price in [d0, d1], or None below MIN_WINDOW_OBS."""
    vals = []
    d = d0
    while d <= d1:
        v = series.get(d.isoformat())
        if v is not None:
            vals.append(v)
        d += timedelta(days=1)
    if len(vals) < MIN_WINDOW_OBS:
        return None
    return median(vals)


def cycle_returns(series, wins, through=None):
    """Realized return per completed cycle, oldest first.

    ret = sell * (1 - TAX) / buy - 1. Cycles whose sell window runs past
    `through` are still in progress and excluded; cycles missing prices in
    either window are omitted. Each cycle notes any expansion launched
    within RELEASE_MARGIN_DAYS of it.
    """
    out = []
    for year in sorted(wins):
        b0, b1, s0, s1 = wins[year]
        if through and s1 > through:
            continue
        buy = window_price(series, b0, b1)
        sell = window_price(series, s0, s1)
        if not buy or sell is None:
            continue
        margin = timedelta(days=RELEASE_MARGIN_DAYS)
        releases = events.releases_between(b0 - margin, s1 + margin)
        out.append(
            {
                "year": year,
                "buy": round(buy),
                "sell": round(sell),
                "ret": round(sell * (1 - TAX) / buy - 1, 3),
                "release": releases[0] if releases else None,
            }
        )
    return out


def hit_rate(cycles):
    """Fraction of cycles with a positive return, or None if empty."""
    if not cycles:
        return None
    return sum(1 for c in cycles if c["ret"] > 0) / len(cycles)


# --- absolute-calendar-date windows (RELEASES entries) --------------------
#
# cycle_windows/anchor_dates re-anchor a recurring day-of-year span to each
# year's actual festival dates. A RELEASES entry is a single concrete date,
# not a recurring span, so these two siblings do plain calendar-day offset
# arithmetic instead - no day-of-year wrap math applies.


def release_windows(releases, buy_offsets=(-90, -30), sell_offsets=(-7, 7)):
    """{label: (buy_start, buy_end, sell_start, sell_end)} for every
    `releases` entry ([(iso_date, label), ...], as season.events.RELEASES).

    The sell window is deliberately kept tight (+7d, not the wider spans a
    recurring festival window gets): S1's own text warns post-release farms
    crash the same materials, so the exit should not linger. The buy
    offsets (-90/-30) are unchanged from the strategy's own stated figures.
    """
    wins = {}
    for iso, label in releases:
        launch = date.fromisoformat(iso)
        b0 = launch + timedelta(days=buy_offsets[0])
        b1 = launch + timedelta(days=buy_offsets[1])
        s0 = launch + timedelta(days=sell_offsets[0])
        s1 = launch + timedelta(days=sell_offsets[1])
        wins[label] = (b0, b1, s0, s1)
    return wins


def release_returns(series, windows, through=None):
    """Realized return per launch, in `windows`' order: {label, buy, sell,
    ret} via window_price and the same sell*(1-TAX)/buy-1 formula as
    cycle_returns. A launch missing observed prices in either window is
    dropped, not guessed, the same rule window_price already applies. A
    launch whose sell window runs past `through` (a date) is still in
    progress and excluded, independent of releases_between's cycle
    annotation (which would be circular once this function exists).
    """
    out = []
    for label, (b0, b1, s0, s1) in windows.items():
        if through and s1 > through:
            continue
        buy = window_price(series, b0, b1)
        sell = window_price(series, s0, s1)
        if not buy or sell is None:
            continue
        out.append(
            {
                "label": label,
                "buy": round(buy),
                "sell": round(sell),
                "ret": round(sell * (1 - TAX) / buy - 1, 3),
            }
        )
    return out
