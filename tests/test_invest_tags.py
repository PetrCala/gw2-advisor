from invest import bankroll, tags


def test_explicit_sets_win_over_the_luxury_price_rule():
    assert tags.classify(24295) == "t6"  # Vial of Powerful Blood
    assert tags.classify(36038) == "festival"  # Trick-or-Treat Bag
    assert tags.classify(19721) == "staples"  # Glob of Ectoplasm
    # a staple stays a staple even priced like luxury
    assert tags.classify(19976, sell_price=600 * 10_000) == "staples"


def test_price_rule_and_default():
    assert tags.classify(12345, sell_price=tags.LUXURY_MIN_PRICE) == "luxury"
    assert tags.classify(12345, sell_price=tags.LUXURY_MIN_PRICE - 1) == "other"
    assert tags.classify(12345) == "other"
    assert tags.classify(12345, sell_price=None) == "other"


def test_every_class_name_is_reachable():
    reachable = {
        tags.classify(next(iter(tags.T6))),
        tags.classify(next(iter(tags.FESTIVAL))),
        tags.classify(next(iter(tags.STAPLES))),
        tags.classify(0, sell_price=tags.LUXURY_MIN_PRICE),
        tags.classify(0),
    }
    assert reachable == set(tags.CLASSES)


def test_the_explicit_sets_do_not_overlap():
    assert not (tags.T6 & tags.FESTIVAL)
    assert not (tags.T6 & tags.STAPLES)
    assert not (tags.FESTIVAL & tags.STAPLES)


def test_bankroll_parses_whole_gold_to_copper():
    assert bankroll.bankroll_copper({"BANKROLL_G": "5000"}) == 5000 * 10_000
    assert bankroll.bankroll_copper({"BANKROLL_G": " 5000 "}) == 5000 * 10_000
    assert bankroll.bankroll_copper({"BANKROLL_G": "5000.9"}) == 5000 * 10_000


def test_bankroll_unset_or_junk_is_none():
    assert bankroll.bankroll_copper({}) is None
    assert bankroll.bankroll_copper({"BANKROLL_G": ""}) is None
    assert bankroll.bankroll_copper({"BANKROLL_G": "lots"}) is None
    assert bankroll.bankroll_copper({"BANKROLL_G": "0"}) is None
    assert bankroll.bankroll_copper({"BANKROLL_G": "-4"}) is None


def test_cap_table_sizes_against_the_bankroll():
    rows = bankroll.cap_table(1_000_000)
    by_name = {r["strategy"]: r for r in rows}
    assert by_name["corners"]["cap_copper"] == 100_000
    assert all(r["cap_copper"] is not None for r in rows)
    assert [r["cap_copper"] is None for r in bankroll.cap_table(None)] == [True] * len(rows)


def test_caps_leave_room_for_cash():
    assert sum(bankroll.CAPS.values()) <= 1.1  # caps may overlap a little by design
