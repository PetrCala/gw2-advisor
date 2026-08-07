from scorers import flip_actions
from scorers.flip import TAX


def test_break_even_sell_clears_the_cost_after_fees():
    buy_at = 1001
    s = flip_actions.break_even_sell(buy_at, TAX)
    assert int((s - 1) * (1 - TAX)) >= buy_at
    assert int((s - 2) * (1 - TAX)) < buy_at  # one copper lower falls short


def test_break_even_sell_matches_rescore_margin_formula():
    # Same shape flip.rescore uses for margin: margin = int((sell_at - 1) *
    # (1 - TAX)) - buy_at. sell_at at the break-even price must zero it out
    # or better; one copper under must not.
    buy_at = 4321
    s = flip_actions.break_even_sell(buy_at, TAX)
    assert int((s - 1) * (1 - TAX)) - buy_at >= 0
    assert int((s - 2) * (1 - TAX)) - buy_at < 0


def test_bail_price_clears_the_dump_value():
    dump_net_per_unit = 850.7
    y = flip_actions.bail_price(dump_net_per_unit, TAX)
    assert int((y - 1) * (1 - TAX)) >= dump_net_per_unit
    assert int((y - 2) * (1 - TAX)) < dump_net_per_unit


def test_bail_price_below_break_even_when_dump_value_is_below_cost():
    # Typical flip: dumping right after buying loses money, so the bail
    # price (floored on the dump value) sits below break-even (floored on
    # cost).
    buy_at = 1001
    dump_net_per_unit = 900  # less than buy_at
    assert flip_actions.bail_price(dump_net_per_unit, TAX) < flip_actions.break_even_sell(
        buy_at, TAX
    )


def test_verdict_act_now_on_all_clear():
    bucket, text = flip_actions.verdict(0.15, "high", -0.05, 5.0, 5.0)
    assert bucket == "act_now"
    assert "act now" in text


def test_verdict_caution_on_one_weak_signal():
    bucket, text = flip_actions.verdict(0.15, "low", -0.05, 5.0, 5.0)
    assert bucket == "caution"
    assert "low confidence" in text


def test_verdict_skip_today_on_two_weak_signals():
    bucket, text = flip_actions.verdict(0.06, "low", -0.05, 5.0, 5.0)
    assert bucket == "skip_today"
    assert "margin barely clears the floor" in text
    assert "low confidence" in text


def test_verdict_skip_today_on_stale_data_plus_weak_margin():
    bucket, text = flip_actions.verdict(0.06, "high", -0.05, None, None)
    assert bucket == "skip_today"
    assert "no reprice data" in text


def test_verdict_caution_on_stale_data_alone():
    bucket, text = flip_actions.verdict(0.15, "high", -0.05, None, None)
    assert bucket == "caution"
    assert "no reprice data" in text


def test_verdict_caution_on_hot_book():
    bucket, text = flip_actions.verdict(0.15, "high", -0.05, 60.0, 60.0)
    assert bucket == "caution"
    assert "repricing fast" in text


def test_verdict_caution_on_thin_exit_floor():
    bucket, text = flip_actions.verdict(0.15, "high", -0.20, 5.0, 5.0)
    assert bucket == "caution"
    assert "exit floor is thin" in text


def test_act_score_ranks_buckets_before_ev_day():
    caution_high_ev = flip_actions.act_score("caution", 1_000_000.0)
    act_now_low_ev = flip_actions.act_score("act_now", 1.0)
    assert act_now_low_ev > caution_high_ev


def test_act_score_breaks_ties_within_a_bucket_by_ev_day():
    a = flip_actions.act_score("act_now", 100.0)
    b = flip_actions.act_score("act_now", 200.0)
    assert b > a
