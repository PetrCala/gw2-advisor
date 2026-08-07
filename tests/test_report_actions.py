import json
from datetime import datetime, timezone
from pathlib import Path

from report import actions

# The fixture doubles as documentation of the actions.json contract (issue #16).
FIX = json.loads(
    (Path(__file__).parent / "fixtures" / "actions.json").read_text(encoding="utf-8")
)

CONTRACT_KEYS = {
    "id", "name", "event", "bucket", "verdict",
    "buy_opens", "buy_closes", "sell_opens", "sell_closes",
    "limit_price", "cur_price", "entry_premium", "suggested_qty",
    "days_to_buy", "days_left", "confidence", "score",
    "med_ret", "hit_rate", "hold_days",
}

HELD = {101: 250, 201: 500}
BUY_ORDERS = [
    {"id": 1, "item_id": 201, "price": 2400, "quantity": 300, "created": "2026-08-06T10:00:00Z"},
    {"id": 2, "item_id": 203, "price": 105, "quantity": 1000, "created": "2026-08-06T10:00:00Z"},
]
SELL_ORDERS = [
    {"id": 3, "item_id": 101, "price": 12000, "quantity": 40, "created": "2026-08-05T10:00:00Z"},
    {"id": 4, "item_id": 102, "price": 1500, "quantity": 70, "created": "2026-08-05T10:00:00Z"},
    {"id": 5, "item_id": 102, "price": 1600, "quantity": 50, "created": "2026-08-05T10:00:00Z"},
]
NOW = datetime(2026, 8, 7, 13, 12, tzinfo=timezone.utc)


def _merge(queue=None, held=HELD):
    q = FIX["queue"] if queue is None else queue
    ids = {r["id"] for r in q}
    return actions.personal_queue(
        q, held,
        actions.aggregate_orders(BUY_ORDERS, ids),
        actions.aggregate_orders(SELL_ORDERS, ids),
    )


def _rendered():
    return actions.render(_merge(), generated=FIX["generated"], now=NOW)


def _line(text, name):
    matches = [l for l in text.splitlines() if name in l]
    assert len(matches) == 1, f"expected one line for {name}, got {matches}"
    return matches[0]


# --- fixture as contract documentation ---

def test_fixture_covers_the_contract():
    assert FIX["queue"], "fixture queue must not be empty"
    for row in FIX["queue"]:
        assert set(row) == CONTRACT_KEYS, row["id"]
        assert row["bucket"] in {"buy_now", "opens_soon", "sell_active", "dormant"}
    for fest in FIX["festivals"]:
        assert set(fest) == {"name", "start", "end", "estimated", "picks"}


# --- aggregate_orders ---

def test_aggregate_orders_sums_qty_and_tracks_min_max():
    aggs = actions.aggregate_orders(SELL_ORDERS)
    assert aggs[102] == {"qty": 120, "min": 1500, "max": 1600}
    assert aggs[101] == {"qty": 40, "min": 12000, "max": 12000}


def test_aggregate_orders_filters_to_ids():
    assert set(actions.aggregate_orders(SELL_ORDERS, {101})) == {101}


# --- sell picks ---

def test_sell_pick_held_and_listed():
    rec = _merge()["sell"][0]
    assert (rec["held"], rec["listed"], rec["listed_min"]) == (250, 40, 12000)
    line = _line(_rendered(), "Wintersday Gift")
    assert "hold 250" in line and "40 listed" in line
    assert "closes 2027-01-10 (156d)" in line
    assert "list at market" in line


def test_sell_pick_listed_only_still_appears():
    rec = _merge()["sell"][1]
    assert (rec["held"], rec["listed"], rec["listed_min"]) == (0, 120, 1500)
    line = _line(_rendered(), "Snow Diamond")
    assert "120 listed (from 0g 15s 00c)" in line
    assert "list at market" not in line


def test_sell_pick_unheld_is_collapsed():
    result = _merge()
    assert all(r["pick"]["id"] != 103 for r in result["sell"])
    assert result["sell_skipped"] == 1
    assert "(1 more sell pick, nothing held or listed)" in _rendered()


# --- buy picks ---

def test_buy_bid_below_limit_gap():
    rec = next(r for r in _merge()["buy"] if r["pick"]["id"] == 201)
    assert (rec["bid_qty"], rec["bid_max"], rec["gap"]) == (300, 2400, 600)
    line = _line(_rendered(), "Zhaitaffy")
    assert "bid 300 @ 0g 24s 00c (0g 06s 00c under limit 0g 30s 00c)" in line
    assert "hold 500 of 1000 suggested" in line


def test_buy_bid_above_limit_flagged():
    rec = next(r for r in _merge()["buy"] if r["pick"]["id"] == 203)
    assert rec["gap"] == -5
    assert "ABOVE limit 0g 01s 00c" in _line(_rendered(), "Piece of Candy Corn")


def test_buy_without_limit_renders():
    rec = next(r for r in _merge()["buy"] if r["pick"]["id"] == 202)
    assert rec["gap"] is None
    line = _line(_rendered(), "Lump of Coal")
    assert "no limit set" in line and "now 0g 00s 45c" in line


def test_buy_no_position_shows_premium_vs_limit():
    line = _line(_rendered(), "Karka Shell")
    assert "no bid, none held" in line
    assert "limit 2g 50s 00c, now 2g 61s 00c (4.4% over)" in line


def test_days_left_null_omits_suffix():
    text = _rendered()
    assert _line(text, "Piece of Candy Corn").endswith("closes 2026-08-20")
    assert "closes" not in _line(text, "Lump of Coal")
    assert _line(text, "Snow Diamond").endswith("closes 2027-01-10")


# --- opens_soon and dormant ---

def test_opens_soon_line():
    line = _line(_rendered(), "Trick-or-Treat Bag")
    assert "buy opens 2026-10-05 (in 59d)" in line
    assert "Halloween" in line


def test_dormant_skipped_entirely():
    result = _merge()
    assert result["dormant"] == 1
    for key in ("sell", "buy", "soon"):
        assert all(r["pick"]["id"] != 401 for r in result[key])
    text = _rendered()
    assert "Mystic Coin" not in text
    assert "1 dormant pick skipped." in text


# --- whole-queue edge cases ---

def test_empty_queue_message():
    out = actions.render(actions.personal_queue([], {}, {}, {}))
    assert "The queue is empty." in out


def test_nothing_actionable_message():
    q = [r for r in FIX["queue"] if r["id"] in (103, 401)]
    out = actions.render(actions.personal_queue(q, {}, {}, {}), now=NOW)
    assert "Nothing actionable" in out
    assert "1 dormant pick skipped." in out


def test_render_is_ascii():
    _rendered().encode("ascii")
