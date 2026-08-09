from report import notify


def _row(**over):
    r = {
        "id": 1, "name": "Test Item", "bucket": "dormant",
        "verdict": "opens Sep 07 (240d)", "buy_opens": "2026-09-07",
        "buy_closes": "2026-09-27", "sell_opens": "2026-12-06",
        "sell_closes": "2026-12-21", "limit_price": 120, "cur_price": 100,
        "suggested_qty": 425, "generated": None,
    }
    r.update(over)
    return r


def test_fmt_money():
    assert notify.fmt_money(123456) == "12g 34s 56c"
    assert notify.fmt_money(156) == "1s 56c"
    assert notify.fmt_money(56) == "56c"


def test_entering_buy_now_emits_buy():
    ev = notify.diff_queues([_row()], [_row(bucket="buy_now")])
    assert [e["kind"] for e in ev] == ["buy"]
    assert ev[0]["line"] == (
        "BUY Test Item: pay up to 1s 20c (now 1s 0c), qty 425, closes 2026-09-27"
    )


def test_staying_in_a_bucket_is_silent():
    assert notify.diff_queues([_row(bucket="buy_now")], [_row(bucket="buy_now")]) == []
    assert notify.diff_queues([_row()], [_row()]) == []


def test_leaving_buy_now_emits_over():
    ev = notify.diff_queues(
        [_row(bucket="buy_now")],
        [_row(bucket="dormant", verdict="too late: +30% over trough")],
    )
    assert [e["kind"] for e in ev] == ["over"]
    assert "too late: +30% over trough" in ev[0]["line"]


def test_new_pick_straight_into_buy_now():
    ev = notify.diff_queues([], [_row(bucket="buy_now")])
    assert [e["kind"] for e in ev] == ["buy"]


def test_entering_sell_window_emits_sell():
    ev = notify.diff_queues([_row()], [_row(bucket="sell_active")])
    assert [e["kind"] for e in ev] == ["sell"]
    assert "closes 2026-12-21" in ev[0]["line"]


def test_opens_soon_from_dormant_only():
    ev = notify.diff_queues([_row()], [_row(bucket="opens_soon")])
    assert [e["kind"] for e in ev] == ["soon"]
    ev = notify.diff_queues(
        [_row(bucket="buy_now")], [_row(bucket="opens_soon")]
    )
    assert [e["kind"] for e in ev] == ["over"]  # no soon on the way down


def test_dropped_buy_now_pick_emits_over():
    ev = notify.diff_queues([_row(bucket="buy_now")], [])
    assert [e["kind"] for e in ev] == ["over"]
    assert "dropped" in ev[0]["line"]


def test_render_comment():
    ev = notify.diff_queues([_row()], [_row(bucket="buy_now")])
    body = notify.render_comment(ev, "2026-08-07T10:00:00Z")
    assert body.startswith("- BUY Test Item")
    assert "as of 2026-08-07T10:00:00Z" in body


def _thesis_row(**over):
    r = {"key": "voe-q3-relic-charms", "id": 89141, "name": "Symbol of Enhancement",
         "bucket": "active"}
    r.update(over)
    return r


def test_diff_theses_fires_on_active_to_catalyst_passed():
    ev = notify.diff_theses(
        [_thesis_row(bucket="active")], [_thesis_row(bucket="catalyst_passed")]
    )
    assert [e["kind"] for e in ev] == ["thesis_catalyst"]
    assert "[THESIS] CATALYST PASSED" in ev[0]["line"]


def test_diff_theses_fires_on_any_bucket_to_invalidated():
    ev = notify.diff_theses(
        [_thesis_row(bucket="watching")], [_thesis_row(bucket="invalidated")]
    )
    assert [e["kind"] for e in ev] == ["thesis_invalidated"]
    assert "[THESIS] INVALIDATION DATE PASSED" in ev[0]["line"]


def test_diff_theses_is_silent_on_staying_in_a_bucket():
    assert notify.diff_theses(
        [_thesis_row(bucket="active")], [_thesis_row(bucket="active")]
    ) == []


def test_diff_theses_ignores_catalyst_passed_not_from_active():
    ev = notify.diff_theses(
        [_thesis_row(bucket="watching")], [_thesis_row(bucket="catalyst_passed")]
    )
    assert ev == []


def test_diff_theses_does_not_repeat_once_already_invalidated():
    ev = notify.diff_theses(
        [_thesis_row(bucket="invalidated")], [_thesis_row(bucket="invalidated")]
    )
    assert ev == []


def test_render_feed_escapes_and_caps():
    log = [
        {"kind": "buy", "id": i, "name": "A & B",
         "line": "BUY A & B <now>", "date": "2026-08-07"}
        for i in range(notify.LOG_CAP + 10)
    ]
    feed = notify.render_feed(log)
    assert feed.count("<item>") == notify.LOG_CAP
    assert "BUY A &amp; B &lt;now&gt;" in feed
    assert "Fri, 07 Aug 2026" in feed
