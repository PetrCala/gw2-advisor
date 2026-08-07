import math
from datetime import date, timedelta

from scorers import flip
from season import cycles, events, score, windows


def _in_span(span, week):
    a, b = span
    return a <= week <= b if a <= b else week >= a or week <= b


def _span_len(span, n=52):
    a, b = span
    return (b - a) % n + 1


# --- windows ---------------------------------------------------------------


def test_trough_and_peak_bracket_the_extremes():
    profile = [math.sin(2 * math.pi * w / 52) for w in range(52)]
    assert _in_span(windows.trough_span(profile), 39)
    assert _in_span(windows.peak_span(profile), 13)


def test_wider_frac_widens_the_span():
    profile = [math.sin(2 * math.pi * w / 52) for w in range(52)]
    narrow = _span_len(windows.trough_span(profile, frac=0.1, max_weeks=None))
    wide = _span_len(windows.trough_span(profile, frac=0.5, max_weeks=None))
    assert wide > narrow


def test_spans_are_clamped_to_max_weeks():
    profile = [math.sin(2 * math.pi * w / 52) for w in range(52)]
    span = windows.trough_span(profile)
    assert _span_len(span) <= windows.MAX_WINDOW_WEEKS
    assert _in_span(span, 39)


def test_trough_span_wraps_the_year_boundary():
    profile = [1.0] * 52
    profile[50], profile[51], profile[0], profile[1] = 0.0, 0.02, 0.03, 0.04
    span = windows.trough_span(profile)
    assert span == (50, 1)
    assert span[0] > span[1]


def test_flat_profile_has_zero_amplitude():
    assert windows.amplitude([2.0] * 52) == 0.0


def test_strength_extremes():
    assert windows.strength([1, -1, 1, -1], [0, 0, 0, 0]) == 1.0
    assert windows.strength([0, 0, 0, 0], [1, -1, 1, -1]) == 0.0


def test_hold_weeks_across_the_wrap():
    assert windows.hold_weeks((48, 50), (0, 2)) == 4


def test_span_to_doy():
    assert windows.span_to_doy((50, 1)) == (351, 14)
    assert windows.span_to_doy((0, 51)) == (1, 365)


# --- cycles ----------------------------------------------------------------

BUY_DOY = (344, 358)  # Dec 10 - Dec 24
SELL_DOY = (10, 20)  # Jan 10 - Jan 20, lands in year+1


def _series(skip_year=None):
    s = {}
    for year in (2019, 2020, 2021):
        if year != skip_year:
            d = date(year, 12, 8)
            for i in range(18):
                s[(d + timedelta(days=i)).isoformat()] = 100.0
        d = date(year + 1, 1, 10)
        for i in range(11):
            s[(d + timedelta(days=i)).isoformat()] = 150.0
    return s


def test_cycle_returns_apply_tax_and_attribute_the_buy_year():
    wins = cycles.cycle_windows(range(2019, 2022), BUY_DOY, SELL_DOY)
    cyc = cycles.cycle_returns(_series(), wins, through=date(2022, 6, 1))
    assert [c["year"] for c in cyc] == [2019, 2020, 2021]
    for c in cyc:
        assert c["buy"] == 100
        assert c["sell"] == 150
        assert c["ret"] == round(150 * 0.85 / 100 - 1, 3)
    assert cycles.hit_rate(cyc) == 1.0


def test_in_progress_cycle_is_excluded():
    wins = cycles.cycle_windows(range(2019, 2022), BUY_DOY, SELL_DOY)
    cyc = cycles.cycle_returns(_series(), wins, through=date(2022, 1, 5))
    assert [c["year"] for c in cyc] == [2019, 2020]


def test_missing_window_drops_only_that_cycle():
    wins = cycles.cycle_windows(range(2019, 2022), BUY_DOY, SELL_DOY)
    cyc = cycles.cycle_returns(_series(skip_year=2020), wins, through=date(2022, 6, 1))
    assert [c["year"] for c in cyc] == [2019, 2021]


def test_window_price_needs_min_observations():
    d0, d1 = date(2020, 3, 1), date(2020, 3, 14)
    thin = {"2020-03-02": 10.0, "2020-03-05": 12.0}
    assert cycles.window_price(thin, d0, d1) is None
    thin["2020-03-09"] = 11.0
    assert cycles.window_price(thin, d0, d1) == 11.0


def test_daily_price_fallback_chain():
    assert cycles.daily_price(5, 1, 9) == 5
    assert cycles.daily_price(None, 1, 9) == 5.0
    assert cycles.daily_price(None, 3, None) == 3
    assert cycles.daily_price(None, None, 7) == 7
    assert cycles.daily_price(None, None, None) is None


def test_anchor_dates_shift_across_new_year():
    span = cycles.anchor_dates((350, 5), 349, date(2017, 12, 12))
    assert span == (date(2017, 12, 13), date(2018, 1, 2))


def test_cycle_release_annotation():
    s = {}
    for i in range(10):
        s[(date(2022, 2, 1) + timedelta(days=i)).isoformat()] = 100.0
        s[(date(2022, 3, 1) + timedelta(days=i)).isoformat()] = 150.0
    wins = {2022: (date(2022, 2, 1), date(2022, 2, 10), date(2022, 3, 1), date(2022, 3, 10))}
    cyc = cycles.cycle_returns(s, wins, through=date(2022, 6, 1))
    assert cyc[0]["release"] == "End of Dragons"


def test_event_windows_skip_years_the_festival_skipped():
    wins = cycles.cycle_windows(
        range(2013, 2017), (88, 108), (150, 170),
        event_name="Super Adventure Festival",
    )
    assert 2013 in wins and 2016 in wins
    assert 2014 not in wins and 2015 not in wins
    b0, _, s0, _ = wins[2016]
    assert s0 > b0


def test_tax_matches_the_flip_scorer():
    assert cycles.TAX == flip.TAX


# --- score -----------------------------------------------------------------


def _cycles(rets, start_year=2018):
    return [
        {"year": start_year + i, "buy": 100, "sell": 120, "ret": r, "release": None}
        for i, r in enumerate(rets)
    ]


def test_score_filters_thin_and_weak_patterns():
    good = _cycles([0.2] * 6)
    assert score.score(_cycles([0.2, 0.2]), 0.6) is None  # too few cycles
    assert score.score(_cycles([0.2, -0.1, 0.2, -0.1]), 0.6) is None  # hit rate
    assert score.score(good, 0.2) is None  # weak seasonality
    assert abs(score.score(good, 0.6) - 0.2) < 1e-9


def test_score_damps_low_sample_counts():
    three = score.score(_cycles([0.2] * 3), 0.6)
    six = score.score(_cycles([0.2] * 6), 0.6)
    assert three == six / 2


def test_confidence_tiers():
    assert score.confidence(_cycles([0.2] * 6)) == "high"
    assert score.confidence(_cycles([0.2] * 5 + [-0.05])) == "medium"
    assert score.confidence(_cycles([0.2] * 3)) == "low"


def test_fmt_window_wraps():
    assert score.fmt_window(date(2025, 12, 20), date(2026, 1, 5)) == "Dec 20 - Jan 05"


def test_fmt_cycles():
    cyc = [
        {"year": 2019, "ret": 0.19, "release": None},
        {"year": 2021, "ret": -0.03, "release": "End of Dragons"},
    ]
    assert score.fmt_cycles(cyc) == "2019 +19% / 2021 -3% (End of Dragons)"


def test_days_to_buy():
    assert score.days_to_buy(date(2026, 8, 7), (314, 328)) == 95
    assert score.days_to_buy(date(2026, 11, 16), (314, 328)) == 0
    assert score.days_to_buy(date(2026, 1, 10), (350, 20)) == 0
    assert score.days_to_buy(date(2026, 10, 27), (350, 20)) == 50


def test_rank_orders_and_truncates():
    cands = [
        {"score": 0.05 + 0.001 * i, "n_cycles": 3} for i in range(score.SEASON_TOP_N + 5)
    ]
    cands.append({"score": 0.05, "n_cycles": 8})
    ranked = score.rank(cands)
    assert len(ranked) == score.SEASON_TOP_N
    assert ranked[0]["score"] == max(c["score"] for c in cands)
    scores = [c["score"] for c in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_breaks_ties_by_cycle_count():
    a = {"score": 0.1, "n_cycles": 3}
    b = {"score": 0.1, "n_cycles": 7}
    assert score.rank([a, b])[0] is b


# --- events ----------------------------------------------------------------


def test_calendar_dates_are_sane():
    for name, runs in events.EVENTS.items():
        for year, (a_iso, b_iso) in runs.items():
            a, b = date.fromisoformat(a_iso), date.fromisoformat(b_iso)
            assert a < b, f"{name} {year}"
            assert 7 <= (b - a).days <= 60, f"{name} {year}"


def test_typical_span_wintersday_wraps():
    start, end = events.typical_span("Wintersday")
    assert 340 <= start <= 355
    assert end < start  # ends in early January
    assert end <= 45


def test_year_span_of_a_skipped_year_is_none():
    assert events.year_span("Super Adventure Festival", 2014) is None
    assert events.year_span("Super Adventure Festival", 2016) == (
        date(2016, 3, 31),
        date(2016, 4, 19),
    )


def test_overlapping_event():
    assert events.overlapping_event((347, 3)) == "Wintersday"
    assert events.overlapping_event((60, 75)) is None  # early March, no festival


def test_releases_between():
    assert events.releases_between(date(2022, 1, 1), date(2022, 12, 31)) == [
        "End of Dragons"
    ]
    assert events.releases_between(date(2016, 1, 1), date(2016, 12, 31)) == []
