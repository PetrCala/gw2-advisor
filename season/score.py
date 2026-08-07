"""Filtering, scoring and display formatting for seasonal candidates.

Score = median net return x hit rate, damped below 6 observed cycles, so a
thin 3-cycle pattern can never outrank an established festival cycle with
the same returns. Confidence tiers make the evidence legible at a glance;
"high" additionally requires the most recent cycle to be positive, a cheap
guard against patterns an expansion just broke.
"""

from statistics import median

from season.cycles import hit_rate

MIN_CYCLES = 3  # fewer completed cycles is anecdote, not seasonality
MIN_MEDIAN_RET = 0.10  # after fees; weeks of locked capital must earn more
MIN_HIT_RATE = 0.60
MIN_STRENGTH = 0.35  # STL seasonal strength; below this the cycle is noise
MIN_AMPLITUDE = 0.10  # log units, roughly a 10% peak-to-trough swing
SEASON_TOP_N = 40

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def score(cycles, strength):
    """Ranking score, or None when the pattern fails a filter."""
    if len(cycles) < MIN_CYCLES:
        return None
    med = median(c["ret"] for c in cycles)
    rate = hit_rate(cycles)
    if med < MIN_MEDIAN_RET or rate < MIN_HIT_RATE or strength < MIN_STRENGTH:
        return None
    return med * rate * min(1.0, len(cycles) / 6)


def confidence(cycles):
    """high, medium or low from cycle count, hit rate and the latest cycle."""
    rate = hit_rate(cycles) or 0.0
    if len(cycles) >= 6 and rate >= 0.8 and cycles[-1]["ret"] > 0:
        return "high"
    if len(cycles) >= 4 and rate >= 0.7:
        return "medium"
    return "low"


def fmt_window(d0, d1):
    """Year-agnostic date range, e.g. "Nov 10 - Nov 24" or "Dec 20 - Jan 05"."""
    return (
        f"{_MONTHS[d0.month - 1]} {d0.day:02d} - "
        f"{_MONTHS[d1.month - 1]} {d1.day:02d}"
    )


def fmt_cycles(cycles):
    """One-line per-cycle history, e.g. "2019 +19% / 2021 -3% (End of Dragons)"."""
    parts = []
    for c in cycles:
        s = f"{c['year']} {c['ret']:+.0%}"
        if c.get("release"):
            s += f" ({c['release']})"
        parts.append(s)
    return " / ".join(parts)


def days_to_buy(today, span_doy):
    """Days until the buy window opens; 0 while inside it. Wrap-aware."""
    a, b = span_doy
    t = min(today.timetuple().tm_yday, 365)
    inside = a <= t <= b if a <= b else t >= a or t <= b
    if inside:
        return 0
    return (a - t) % 365


def rank(candidates):
    """Top SEASON_TOP_N by score, ties broken by cycle count."""
    ordered = sorted(candidates, key=lambda p: (-p["score"], -p["n_cycles"]))
    return ordered[:SEASON_TOP_N]
