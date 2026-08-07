import json
from datetime import date

from report import track


def _pick(**over):
    p = {
        "id": 1, "name": "Test Item", "rarity": "Rare",
        "buy_at": 100, "sell_at": 200, "qty": 10, "margin": 69,
        "round_trip_days": 1.0,
    }
    p.update(over)
    return p


def _row(**over):
    r = {
        "id": 1, "buy_price": 100, "sell_price": 200,
        "1d_buy_sold": 40, "1d_sell_sold": 40,
    }
    r.update(over)
    return r


def _queue_row(**over):
    r = {
        "id": 7, "name": "Season Item", "event": "Halloween", "bucket": "buy_now",
        "limit_price": 1000, "cur_price": 900, "suggested_qty": 20,
        "buy_closes": "2026-08-20", "sell_opens": "2026-10-01",
        "sell_closes": "2026-10-20", "med_ret": 0.4,
    }
    r.update(over)
    return r


class FakeStore:
    """collector.s3store.Store's read/write surface, in memory."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def get_json_gz(self, key):
        return self.objects.get(key)

    def put_json_gz(self, key, payload):
        self.objects[key] = json.loads(json.dumps(payload))


D0 = date(2026, 8, 7)


def _day(n):
    return date.fromordinal(D0.toordinal() + n)


# --- flip simulation -----------------------------------------------------


def test_a_pick_that_fills_and_sells_books_the_predicted_margin():
    pos = track.open_flip(_pick(), D0)
    track.tick_flip(pos, _row(), D0)
    assert pos["filled"] == 10 and pos["sold"] == 10
    rec = track.close_flip(pos, D0)
    assert rec["outcome"] == "closed"
    assert rec["pred_net"] == 690
    assert rec["net"] == 691.5  # 10 units at (200-1) net of tax, less 10x100
    assert 0.99 < rec["net_pct"] < 1.01
    assert rec["rt_days"] == 1


def test_an_order_the_market_never_reaches_never_fills():
    pos = track.open_flip(_pick(), D0)
    for n in range(3):
        track.tick_flip(pos, _row(buy_price=140), _day(n))
    assert pos["filled"] == 0
    assert pos["buy_front_days"] == 0
    rec = track.close_flip(pos, _day(3))
    assert rec["outcome"] == "unfilled"
    assert rec["net"] == 0
    assert rec["capture_needed"] is None  # no flow passed the order to capture


def test_capture_needed_is_the_share_of_front_flow_the_lot_demanded():
    pos = track.open_flip(_pick(qty=20, round_trip_days=2.0), D0)
    track.tick_flip(pos, _row(**{"1d_buy_sold": 40}), D0)
    track.tick_flip(pos, _row(**{"1d_buy_sold": 40}), _day(1))
    rec = track.close_flip(pos, _day(2))
    assert rec["capture_needed"] == 0.25  # 20 units of the 80 that traded


def test_flow_past_the_predicted_round_trip_is_not_charged_to_capture():
    pos = track.open_flip(_pick(qty=100), D0)  # horizon of one day
    for n in range(4):
        track.tick_flip(pos, _row(), _day(n))
    rec = track.close_flip(pos, _day(4))
    assert pos["buy_front_days"] == 4
    assert rec["capture_needed"] == 2.5  # 100 wanted against day one's 40


def test_unsold_units_are_marked_at_the_last_bid_net_of_fees():
    pos = track.open_flip(_pick(), D0)
    track.tick_flip(pos, _row(sell_price=150), D0)  # bought, but undercut on the way out
    assert pos["filled"] == 10 and pos["sold"] == 0
    rec = track.close_flip(pos, _day(1))
    assert rec["outcome"] == "partial"
    assert rec["net"] == round(10 * 100 * 0.85 - 1000, 1)  # dumped into the bid
    assert rec["net_pct"] < 0


def test_partial_fills_accumulate_across_days():
    pos = track.open_flip(_pick(qty=10), D0)
    track.tick_flip(pos, _row(**{"1d_buy_sold": 20}), D0)
    assert pos["filled"] == 5 and pos["sold"] == 5
    track.tick_flip(pos, _row(**{"1d_buy_sold": 20}), _day(1))
    assert pos["filled"] == 10 and pos["sold"] == 10
    assert pos["filled_day"] == 2 and pos["sold_day"] == 2


def test_a_finished_round_trip_resolves_and_a_live_one_does_not():
    pos = track.open_flip(_pick(), D0)
    assert not track.is_done(pos, D0)
    track.tick_flip(pos, _row(), D0)
    assert track.is_done(pos, D0)


def test_an_unfilled_position_resolves_at_the_horizon():
    pos = track.open_flip(_pick(), D0)
    assert not track.is_done(pos, _day(track.FLIP_HORIZON_DAYS - 1))
    assert track.is_done(pos, _day(track.FLIP_HORIZON_DAYS))


# --- seasonal simulation -------------------------------------------------


def test_a_seasonal_hold_buys_under_its_ceiling_and_scores_at_the_window():
    pos = track.open_season(_queue_row(), D0)
    track.tick_season(pos, _row(sell_price=900, **{"1d_sell_sold": 80}), D0)
    assert pos["filled"] == 20 and pos["spent"] == 18000
    track.tick_season(pos, _row(sell_price=2000), date(2026, 10, 5))
    assert pos["window_days"] == 1
    rec = track.close_season(pos, date(2026, 10, 21))
    assert rec["outcome"] == "closed"
    assert rec["avg_cost"] == 900
    assert rec["ret"] == round((2000 * 0.85 - 900) / 900, 3)
    assert rec["pred_ret"] == 0.4


def test_a_seasonal_hold_will_not_buy_above_its_ceiling():
    pos = track.open_season(_queue_row(), D0)
    track.tick_season(pos, _row(sell_price=1100), D0)
    assert pos["filled"] == 0
    assert track.is_done(pos, date(2026, 8, 21))  # buy window gone, nothing bought
    rec = track.close_season(pos, date(2026, 8, 21))
    assert rec["outcome"] == "unfilled" and rec["ret"] is None


def test_a_hold_that_misses_its_window_is_marked_not_scored():
    pos = track.open_season(_queue_row(), D0)
    track.tick_season(pos, _row(sell_price=900, **{"1d_sell_sold": 80}), D0)
    rec = track.close_season(pos, date(2026, 10, 21))
    assert rec["outcome"] == "marked"
    assert rec["window_days"] == 0


def test_a_queue_row_without_a_limit_or_a_size_is_not_tracked():
    assert track.open_season(_queue_row(limit_price=None), D0) is None
    assert track.open_season(_queue_row(suggested_qty=None), D0) is None
    assert track.open_season(_queue_row(sell_closes=None), D0) is None


# --- the daily roll ------------------------------------------------------


def _empty():
    return {"since": None, "positions": [], "records": []}


def test_the_first_roll_opens_todays_picks_and_resolves_nothing():
    state, stats = track.roll(_empty(), [_pick()], [_queue_row()], {}, D0)
    assert stats == {"opened": 2, "closed": 0, "dropped": 0}
    assert {p["kind"] for p in state["positions"]} == {"flip", "season"}
    assert state["since"] == D0.isoformat()
    assert state["records"] == []


def test_an_item_already_tracked_is_not_opened_twice():
    state, _ = track.roll(_empty(), [_pick()], [], {}, D0)
    state, stats = track.roll(state, [_pick()], [], {}, _day(1))
    assert stats["opened"] == 0
    assert len(state["positions"]) == 1


def test_a_resolved_pick_moves_to_the_record_and_the_item_reopens():
    state, _ = track.roll(_empty(), [_pick()], [], {}, D0)
    state, stats = track.roll(state, [_pick()], [], {1: _row()}, _day(1))
    assert stats["closed"] == 1 and stats["opened"] == 1
    assert [r["outcome"] for r in state["records"]] == ["closed"]
    assert len(state["positions"]) == 1  # reopened at today's prices


def test_a_second_run_on_the_same_day_does_not_double_count():
    state, _ = track.roll(_empty(), [_pick()], [], {}, D0)
    once, _ = track.roll(state, [_pick()], [], {1: _row(**{"1d_buy_sold": 20})}, _day(1))
    twice, stats = track.roll(
        once, [_pick()], [], {1: _row(**{"1d_buy_sold": 20})}, _day(1)
    )
    assert stats["opened"] == 0 and stats["closed"] == 0
    assert [p["filled"] for p in twice["positions"] if p["kind"] == "flip"] == [5]


def test_the_open_book_is_capped():
    picks = [_pick(id=i, name=f"Item {i}") for i in range(track.OPEN_CAP + 5)]
    state, stats = track.roll(_empty(), picks, [], {}, D0)
    assert len(state["positions"]) == min(track.FLIP_PER_DAY, track.OPEN_CAP)
    state["positions"] = [track.open_flip(p, D0) for p in picks]
    state, stats = track.roll(state, [], [], {}, _day(1))
    assert len(state["positions"]) == track.OPEN_CAP
    assert stats["dropped"] == 5


def test_the_record_is_capped():
    state = _empty()
    state["records"] = [
        track.close_flip(track.open_flip(_pick(id=i), D0), D0)
        for i in range(track.CLOSED_CAP + 10)
    ]
    state, _ = track.roll(state, [], [], {}, D0)
    assert len(state["records"]) == track.CLOSED_CAP
    assert state["records"][0]["id"] == 10  # the oldest fell off, not the newest


def test_positions_from_an_older_schema_are_dropped_not_crashed_on():
    state = _empty()
    state["positions"] = [{"v": 0, "kind": "flip", "id": 1}]
    state, stats = track.roll(state, [_pick()], [], {1: _row()}, D0)
    assert [p["v"] for p in state["positions"]] == [track.SCHEMA]
    assert stats["opened"] == 1 and stats["closed"] == 0


def test_only_buy_now_queue_rows_are_tracked():
    state, stats = track.roll(
        _empty(), [], [_queue_row(bucket="opens_soon"), _queue_row(id=8)], {}, D0
    )
    assert stats["opened"] == 1
    assert [p["id"] for p in state["positions"]] == [8]


# --- summary and page ----------------------------------------------------


def _resolved_state():
    """One pick that round-tripped, one the market never came down to."""
    dud = {2: _row(buy_price=140)}
    state, _ = track.roll(_empty(), [_pick(), _pick(id=2, name="Dud")], [], {}, D0)
    state, _ = track.roll(state, [], [], {1: _row(), **dud}, _day(1))
    state, _ = track.roll(state, [], [], dud, _day(track.FLIP_HORIZON_DAYS))
    return state


def test_summary_counts_outcomes_and_compares_against_the_assumption():
    s = track.summarize(_resolved_state())
    assert s["flip"]["n"] == 2
    assert s["flip"]["completed"] == 1 and s["flip"]["unfilled"] == 1
    assert s["flip"]["never_front"] == 1
    assert s["flip"]["completed_pct"] == 0.5
    assert s["flip"]["pred_net"] == 1380  # both picks promised 690
    assert s["flip"]["net"] == 692
    assert s["flip"]["med_capture_needed"] == 0.25
    assert s["flip"]["capture_assumed"] == track.CAPTURE
    assert s["open"] == {"flip": 0, "season": 0}


def test_summary_of_an_empty_record_is_all_zeroes():
    s = track.summarize(_empty())
    assert s["flip"]["n"] == 0 and s["flip"]["net_pct"] is None
    assert s["season"]["med_ret"] is None


def test_the_page_renders_every_placeholder():
    state = _resolved_state()
    state["positions"].append(track.open_season(_queue_row(), D0))
    html = track.render(state, track.summarize(state), "2026-08-08 04:20 UTC")
    for placeholder in ("GENERATED", "SINCE", "OPEN", "SCORE", "FLIPS",
                        "SEASON_META", "SEASON_OPEN", "SEASON_CLOSED"):
        assert f"__{placeholder}__" not in html
    assert "Test Item" in html and "Season Item" in html
    assert "Capture needed" in html
    assert "2026-08-08 04:20 UTC" in html


def test_the_page_escapes_item_names():
    state = _empty()
    state["positions"] = [track.open_season(_queue_row(name="A & B"), D0)]
    html = track.render(state, track.summarize(state), "now")
    assert "A &amp; B" in html and "A & B" not in html


def test_money_renders_negative_nets():
    assert "-" in track.money(-12345)
    assert "1g" in track.money(12345)


# --- entry point ---------------------------------------------------------


def _site(tmp_path, monkeypatch, picks=None, queue=None):
    monkeypatch.setattr(track, "SITE", tmp_path)
    (tmp_path / "data.json").write_text(
        json.dumps({"picks": picks if picks is not None else [_pick()]}),
        encoding="utf-8",
    )
    (tmp_path / "actions.json").write_text(
        json.dumps({"queue": queue if queue is not None else [_queue_row()]}),
        encoding="utf-8",
    )
    return tmp_path


def test_main_writes_the_state_and_the_page(tmp_path, monkeypatch):
    site = _site(tmp_path, monkeypatch)
    store = FakeStore()
    track.main(store=store, snapshot=[_row()], today=D0)

    assert set(store.objects) == {track.OPEN_KEY, track.CLOSED_KEY}
    assert store.objects[track.OPEN_KEY]["since"] == D0.isoformat()
    assert len(store.objects[track.OPEN_KEY]["positions"]) == 2
    assert store.objects[track.CLOSED_KEY]["records"] == []
    assert (site / "track.html").exists()
    doc = json.loads((site / "track.json").read_text(encoding="utf-8"))
    assert doc["summary"]["flip"]["n"] == 0
    assert len(doc["open"]) == 2


def test_main_scores_the_next_day_against_the_stored_state(tmp_path, monkeypatch):
    _site(tmp_path, monkeypatch)
    store = FakeStore()
    track.main(store=store, snapshot=[_row()], today=D0)
    track.main(store=store, snapshot=[_row()], today=_day(1))

    records = store.objects[track.CLOSED_KEY]["records"]
    assert [r["outcome"] for r in records] == ["closed"]
    assert records[0]["net_pct"] > 0.99
    doc = json.loads((tmp_path / "track.json").read_text(encoding="utf-8"))
    assert doc["summary"]["since"] == D0.isoformat()
    assert doc["summary"]["flip"]["completed"] == 1


def test_main_without_a_report_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(track, "SITE", tmp_path)
    store = FakeStore()
    track.main(store=store, snapshot=[_row()], today=D0)
    assert store.objects == {}


def test_main_tolerates_a_missing_action_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(track, "SITE", tmp_path)
    (tmp_path / "data.json").write_text(
        json.dumps({"picks": [_pick()]}), encoding="utf-8"
    )
    store = FakeStore()
    track.main(store=store, snapshot=[_row()], today=D0)
    assert len(store.objects[track.OPEN_KEY]["positions"]) == 1
