"""Buy/sell window detection on a periodic weekly profile.

The profile is a plain list of 52 floats: the average STL seasonal component
per week of year, in log-price units. The buy window is the contiguous span
around the seasonal trough, the sell window the span around the peak. Spans
are (start_week, end_week) inclusive, 0-based; start > end means the span
wraps the year boundary.
"""

WEEKS = 52
MAX_WINDOW_WEEKS = 4  # a window wider than a month is not an actionable trade


def weekly_profile(weeks, values):
    """Mean value per week of year from parallel lists; empty weeks get the
    overall mean so the profile stays fully defined."""
    sums = [0.0] * WEEKS
    counts = [0] * WEEKS
    for w, v in zip(weeks, values):
        sums[w] += v
        counts[w] += 1
    total = sum(counts)
    if not total:
        return [0.0] * WEEKS
    overall = sum(sums) / total
    return [sums[i] / counts[i] if counts[i] else overall for i in range(WEEKS)]


def amplitude(profile):
    """Peak-to-trough swing, in the profile's own units."""
    return max(profile) - min(profile)


def _var(xs):
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)


def strength(seasonal, resid):
    """Hyndman seasonal strength: 1 - Var(resid) / Var(seasonal + resid)."""
    both = [s + r for s, r in zip(seasonal, resid)]
    vb = _var(both)
    if vb == 0:
        return 0.0
    return max(0.0, 1.0 - _var(resid) / vb)


def _extremum_span(profile, index, marked, max_weeks):
    """The contiguous circular run of marked weeks containing index, clamped
    to max_weeks centered on the extremum (broad profiles otherwise mark a
    third of the year, which dilutes window prices and is untradable)."""
    n = len(profile)
    start = index
    for _ in range(n - 1):
        if marked[(start - 1) % n]:
            start = (start - 1) % n
        else:
            break
    end = index
    for _ in range(n - 1):
        if marked[(end + 1) % n]:
            end = (end + 1) % n
        else:
            break
    if max_weeks and (end - start) % n + 1 > max_weeks:
        start = (index - (max_weeks - 1) // 2) % n
        end = (start + max_weeks - 1) % n
    return start, end


def trough_span(profile, frac=0.25, max_weeks=MAX_WINDOW_WEEKS):
    """Weeks within frac of the amplitude above the minimum, around it."""
    lo = min(profile)
    cut = lo + frac * amplitude(profile)
    marked = [v <= cut for v in profile]
    return _extremum_span(profile, profile.index(lo), marked, max_weeks)


def peak_span(profile, frac=0.25, max_weeks=MAX_WINDOW_WEEKS):
    """Weeks within frac of the amplitude below the maximum, around it."""
    hi = max(profile)
    cut = hi - frac * amplitude(profile)
    marked = [v >= cut for v in profile]
    return _extremum_span(profile, profile.index(hi), marked, max_weeks)


def _mid(span):
    a, b = span
    length = (b - a) % WEEKS + 1
    return (a + length // 2) % WEEKS


def hold_weeks(buy_span, sell_span):
    """Weeks from the buy-span middle forward to the sell-span middle."""
    return (_mid(sell_span) - _mid(buy_span)) % WEEKS


def span_to_doy(span):
    """Week span to a 1-based day-of-year span; week w covers 7w+1..7w+7."""
    a, b = span
    start = 7 * a + 1
    end = 365 if b == WEEKS - 1 else 7 * b + 7
    return start, end
