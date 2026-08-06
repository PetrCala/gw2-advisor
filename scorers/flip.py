"""Flip scorer v1: rank items by expected profit per day from buy-order to
sell-listing round trips, executed manually in game.

The model, stated plainly (every tunable is a constant below):

- We place a buy order 1c above the best buy and later list 1c under the
  best sell, so we sit at the front of both queues.
- The trading post takes 15% of the sale (5% listing fee, 10% tax).
- Daily flow per side comes from datawars2 7-day fill counts: buy_sold is
  units filled against buy orders (our buy side), sell_sold is units bought
  off sell listings (our sell side).
- While our order sits at the front we capture CAPTURE of that side's flow;
  competitors repricing against us eat the rest.
- EV/day = margin / per-unit cycle time. In this linear model it does not
  depend on lot size (capital is recycled); the suggested qty is what fits
  under CAPITAL_PER_ITEM and a MAX_ROUND_TRIP_DAYS round trip.

Inputs are datawars2 snapshot rows, optionally with buy_price, sell_price,
buy_quantity, sell_quantity overwritten from our own fresher collector state.
"""

TAX = 0.15
CAPTURE = 0.25
MAX_ROUND_TRIP_DAYS = 2.0
CAPITAL_PER_ITEM = 1_000_000  # 100 gold, in copper
MIN_MARGIN_PCT = 0.05
MIN_FLOW = 24.0  # units/day on both sides (7d average); one an hour
TOP_N = 50


def score_item(it):
    """Score one snapshot row; None when it fails a filter."""
    buy, sell = it.get("buy_price"), it.get("sell_price")
    if not buy or not sell or sell <= buy:
        return None
    if it.get("vendor_value") and sell <= it["vendor_value"]:
        return None  # market price below the vendor floor is a data glitch

    buy_flow = (it.get("7d_buy_sold") or 0) / 7
    sell_flow = (it.get("7d_sell_sold") or 0) / 7
    if buy_flow < MIN_FLOW or sell_flow < MIN_FLOW:
        return None

    cost = buy + 1
    net = int((sell - 1) * (1 - TAX))
    margin = net - cost
    if margin <= 0 or margin / cost < MIN_MARGIN_PCT:
        return None

    per_unit_days = 1 / (CAPTURE * buy_flow) + 1 / (CAPTURE * sell_flow)
    qty = int(min(MAX_ROUND_TRIP_DAYS / per_unit_days, CAPITAL_PER_ITEM // cost))
    if qty < 1:
        return None

    return {
        "id": it["id"],
        "name": it.get("name") or f"item {it['id']}",
        "rarity": it.get("rarity") or "",
        "buy_at": cost,
        "sell_at": sell - 1,
        "margin": margin,
        "margin_pct": margin / cost,
        "qty": qty,
        "capital": qty * cost,
        "buy_flow": round(buy_flow),
        "sell_flow": round(sell_flow),
        "round_trip_days": qty * per_unit_days,
        "ev_day": margin / per_unit_days,
        "confidence": confidence(it),
    }


def confidence(it):
    """high, medium or low: how much the last week backs today's numbers."""
    checks = 0

    # The spread survived over week-average prices, not just this instant.
    a7b, a7s = it.get("7d_buy_price_avg"), it.get("7d_sell_price_avg")
    if a7b and a7s and (a7s - 1) * (1 - TAX) - (a7b + 1) > 0:
        checks += 1

    # The sell price stayed inside a narrow band all week.
    lo, hi = it.get("7d_sell_price_min"), it.get("7d_sell_price_max")
    if lo and hi and a7s and (hi - lo) / a7s < 0.15:
        checks += 1

    # Yesterday's flow roughly matches the weekly average on both sides.
    d1b, d1s = it.get("1d_buy_sold") or 0, it.get("1d_sell_sold") or 0
    d7b, d7s = it.get("7d_buy_sold") or 0, it.get("7d_sell_sold") or 0
    if d7b and d7s and 0.5 < 7 * d1b / d7b < 2 and 0.5 < 7 * d1s / d7s < 2:
        checks += 1

    return ("low", "low", "medium", "high")[checks]


def score_all(items):
    """Score every snapshot row, return the top TOP_N by EV/day."""
    scored = [s for s in (score_item(it) for it in items) if s]
    scored.sort(key=lambda s: s["ev_day"], reverse=True)
    return scored[:TOP_N]
