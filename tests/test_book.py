from features import book
from features.reprice import reprice_rates

BUYS = [[100, 5000], [95, 200], [94, 10000]]  # best first
SELLS = [[110, 5000], [115, 200], [116, 10000]]  # best first


def test_buy_price_stays_at_top_when_book_is_thick():
    # 0.25 days of 1000/day flow tolerates a 250-unit queue; the 5000 at
    # the top level blows the budget, so we outbid the best.
    assert book.choose_buy_price(BUYS, 1000, 0.25) == 101


def test_buy_price_walks_into_gap_when_queue_is_acceptable():
    # 100k/day flow tolerates 25k queued: past the 5000 and the 200,
    # down to 1c above the 94 level.
    assert book.choose_buy_price(BUYS, 100_000, 0.25) == 95


def test_sell_price_mirrors():
    assert book.choose_sell_price(SELLS, 1000, 0.25) == 109
    assert book.choose_sell_price(SELLS, 100_000, 0.25) == 115


def test_empty_book_side():
    assert book.choose_buy_price([], 1000, 0.25) is None
    assert book.choose_sell_price([], 1000, 0.25) is None


def test_queue_ahead():
    assert book.queue_ahead(BUYS, 96, "buy") == 5000
    assert book.queue_ahead(BUYS, 101, "buy") == 0
    assert book.queue_ahead(SELLS, 114, "sell") == 5000


def test_dump_value_walks_levels():
    assert book.dump_value(BUYS, 5100) == 5000 * 100 + 100 * 95


def test_dump_value_past_book_depth_uses_last_level():
    assert book.dump_value(BUYS, 20000) == 5000 * 100 + 200 * 95 + 10000 * 94 + 4800 * 94


def test_reprice_rates_counts_small_steps_only():
    rows = [
        (0, 1, 100, 200),
        (600, 1, 101, 199),  # outbid +1, undercut -1
        (1200, 1, 103, 190),  # +2 outbid, -9 not an undercut
        (1800, 1, 150, 190),  # +47 not an outbid
    ]
    rates = reprice_rates(rows, 2.0)
    assert rates[1] == (1.0, 0.5)  # 2 outbids, 1 undercut over 2 days
