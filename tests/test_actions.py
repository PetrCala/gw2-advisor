from datetime import date

from scorers import flip
from season import actions


def _cyc(pairs, start_year=2020):
    return [
        {"year": start_year + i, "buy": b, "sell": s,
         "ret": round(s * 0.85 / b - 1, 3), "release": None}
        for i, (b, s) in enumerate(pairs)
    ]


def _pick(**over):
    p = {
        "id": 1, "name": "Test Item", "rarity": "Fine", "event": None,
        "buy_doy": [250, 270], "sell_doy": [340, 355], "hold_days": 90,
        "cycles": _cyc([(100, 160), (110, 170), (120, 180)]),
        "n_cycles": 3, "med_ret": 0.3, "hit_rate": 1.0, "worst_ret": 0.2,
        "last_ret": 0.3, "strength": 0.6, "cur_price": 100,
        "window_flow": 100, "confidence": "low", "score": 0.5,
    }
    p.update(over)
    return p


# --- prices and sizing ------------------------------------------------------


def test_limit_price_from_recent_sells():
    cyc = _cyc([(100, 160), (110, 170), (120, 180)])
    assert actions.limit_price(cyc) == int(170 * 0.85 / 1.20)


def test_limit_price_ignores_old_cycles():
    cyc = _cyc([(10, 10), (10, 10), (100, 160), (110, 170), (120, 180)])
    assert actions.limit_price(cyc) == int(170 * 0.85 / 1.20)


def test_entry_premium_uses_recent_buys():
    cyc = _cyc([(100, 160), (110, 170), (120, 180)])
    assert actions.entry_premium(220, cyc) == 1.0
    assert actions.entry_premium(None, cyc) is None


def test_suggested_qty_flow_and_capital_bounds():
    assert actions.suggested_qty(100, 10, 120) == 250  # 25% of flow wins
    assert actions.suggested_qty(10000, 20, 120) == actions.SEASON_CAPITAL // 120
    assert actions.suggested_qty(None, 10, 120) is None
    assert actions.suggested_qty(100, 10, None) is None


def test_capture_matches_the_flip_scorer():
    assert actions.CAPTURE == flip.CAPTURE


# --- span dates -------------------------------------------------------------


def test_span_dates_inside_a_wrapped_span():
    d0, d1 = actions.span_dates(date(2026, 1, 10), (350, 20))
    assert d0 == date(2025, 12, 16)
    assert d1 == date(2026, 1, 20)


def test_span_dates_next_occurrence():
    d0, d1 = actions.span_dates(date(2026, 8, 20), (250, 270))
    assert d0 == date(2026, 9, 7)
    assert d1 == date(2026, 9, 27)
    d0, _ = actions.span_dates(date(2026, 10, 1), (250, 270))
    assert d0 == date(2027, 9, 7)


def test_anchored_span_follows_the_announced_run():
    # Four Winds runs Aug 11 in 2026 against a typical Jul 31 start, so a
    # window sitting 15 days before the typical start moves with it.
    span = actions.anchored_span(
        date(2026, 8, 7), (197, 224), "Festival of the Four Winds"
    )
    assert span == (date(2026, 7, 27), date(2026, 8, 23), True)


def test_anchored_span_falls_back_when_unannounced():
    today = date(2026, 8, 7)
    span_doy = (289, 310)
    start, end, anchored = actions.anchored_span(today, span_doy, "Halloween")
    assert not anchored  # the 2026 run is not in the calendar yet
    assert (start, end) == actions.span_dates(today, span_doy)


def test_anchored_span_ignores_a_run_already_over():
    # Lunar New Year 2026 ended in February and 2027 is unannounced.
    today = date(2026, 8, 7)
    span_doy = (20, 41)
    start, end, anchored = actions.anchored_span(today, span_doy, "Lunar New Year")
    assert not anchored
    assert (start, end) == actions.span_dates(today, span_doy)


def test_anchored_span_without_an_event():
    today = date(2026, 8, 20)
    assert actions.anchored_span(today, (250, 270)) == (
        date(2026, 9, 7), date(2026, 9, 27), False
    )


# --- verdicts ---------------------------------------------------------------


def test_enrich_buy_now():
    p = actions.enrich(_pick(), date(2026, 9, 10))
    assert p["bucket"] == "buy_now"
    assert p["verdict"] == "buy now, closes Sep 27"
    assert p["days_to_buy"] == 0
    assert p["days_left"] == 17
    assert p["limit_price"] == int(170 * 0.85 / 1.20)
    assert p["suggested_qty"] == 425  # 25% of 100/day over 17 days


def test_enrich_too_late_over_trough():
    p = actions.enrich(_pick(cur_price=300), date(2026, 9, 10))
    assert p["bucket"] == "dormant"
    assert p["verdict"] == "too late: +173% over trough"


def test_enrich_too_late_over_ceiling_only():
    cyc = _cyc([(100, 120), (100, 120), (100, 120)])
    p = actions.enrich(_pick(cycles=cyc, cur_price=90), date(2026, 9, 10))
    assert p["bucket"] == "dormant"
    assert p["verdict"] == "too late: over the ceiling"


def test_enrich_opens_soon():
    p = actions.enrich(_pick(), date(2026, 8, 20))
    assert p["bucket"] == "opens_soon"
    assert p["verdict"] == "opens Sep 07 (18d)"
    assert p["days_left"] is None
    assert p["suggested_qty"] == 525  # full 21-day window at 25% of flow


def test_enrich_sell_active():
    p = actions.enrich(_pick(), date(2026, 12, 10))
    assert p["bucket"] == "sell_active"
    assert p["verdict"] == "sell window, closes Dec 21"


def test_enrich_dormant_far_out():
    p = actions.enrich(_pick(), date(2026, 1, 10))
    assert p["bucket"] == "dormant"
    assert p["verdict"] == "opens Sep 07 (240d)"


def test_enrich_prefers_fresh_price():
    p = actions.enrich(_pick(cur_price=300), date(2026, 9, 10), fresh_price=100)
    assert p["bucket"] == "buy_now"
    assert p["cur_price"] == 100


def test_enrich_delays_a_buy_to_the_announced_run():
    # Typical Four Winds dates put this window at Jul 30 - Aug 26, but the
    # 2026 run starts 11 days late, so Aug 07 is not a buy yet.
    p = actions.enrich(
        _pick(event="Festival of the Four Winds", buy_doy=[211, 238]),
        date(2026, 8, 7),
    )
    assert p["buy_opens"] == "2026-08-10"
    assert p["buy_closes"] == "2026-09-06"
    assert p["bucket"] == "opens_soon"
    assert p["days_to_buy"] == 3


def test_enrich_anchors_the_sell_window():
    p = actions.enrich(
        _pick(event="Festival of the Four Winds",
              buy_doy=[100, 120], sell_doy=[212, 233]),
        date(2026, 8, 30),
    )
    assert p["sell_opens"] == "2026-08-11"  # typical span says Jul 31
    assert p["bucket"] == "sell_active"
    assert p["verdict"] == "sell window, closes Sep 01"


def test_enrich_keeps_typical_dates_while_unannounced():
    p = actions.enrich(_pick(event="Halloween", buy_doy=[211, 238]), date(2026, 8, 7))
    assert p["buy_opens"] == "2026-07-30"
    assert p["bucket"] == "buy_now"


def test_act_orders_buckets_before_score():
    buy = actions.enrich(_pick(score=0.1), date(2026, 9, 10))
    soon = actions.enrich(_pick(score=1.5), date(2026, 8, 20))
    sell = actions.enrich(_pick(score=1.5), date(2026, 12, 10))
    dormant = actions.enrich(_pick(score=1.5), date(2026, 1, 10))
    assert buy["act"] > soon["act"] > sell["act"] > dormant["act"]


# --- festivals --------------------------------------------------------------


def test_next_festivals_announced_and_estimated():
    fests = {f["name"]: f for f in actions.next_festivals(date(2026, 8, 7))}
    fw = fests["Festival of the Four Winds"]
    assert fw["start"] == "2026-08-11"
    assert not fw["estimated"]
    assert fests["Halloween"]["estimated"]  # 2026 run not announced yet
    assert fests["Halloween"]["start"].startswith("2026-10")


def test_next_festivals_counts_linked_picks():
    picks = [{"event": "Halloween"}, {"event": "Halloween"}, {"event": None}]
    fests = {f["name"]: f for f in actions.next_festivals(date(2026, 8, 7), picks)}
    assert fests["Halloween"]["picks"] == 2
    assert fests["Wintersday"]["picks"] == 0


def test_unconfirmed_soon_flags_only_imminent_estimates():
    soon = actions.unconfirmed_soon(date(2026, 9, 15))
    assert [f["name"] for f in soon] == ["Halloween"]
