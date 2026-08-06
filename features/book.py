"""Order-book depth features from /v2/commerce/listings.

Books arrive as [[price, qty], ...]: buys sorted best (highest) first,
sells sorted best (lowest) first. Prices in copper.

The pricing idea: blindly going best+-1c overpays when the top of the book
is a thin crowd over a gap. Instead we walk the book away from the best
price and take the deepest level whose queue ahead of us still clears
within a wait budget at the item's observed flow. Existing orders ahead of
ours absorb the side's full flow before we fill at all, so the wait is
queue_ahead / flow, not capture-scaled.
"""


def choose_buy_price(buys, flow_per_day, wait_tolerance_days):
    """Our buy-order price, or None if the book side is empty.

    Candidates are 1c above each level. Walking down the book raises margin
    but queues us behind everything priced higher; accept the deepest level
    whose queue fills within the wait budget.
    """
    if not buys or flow_per_day <= 0:
        return None
    max_queue = wait_tolerance_days * flow_per_day
    price, ahead = buys[0][0] + 1, 0
    for prev, nxt in zip(buys, buys[1:]):
        ahead += prev[1]
        if ahead > max_queue:
            break
        price = nxt[0] + 1
    return price


def choose_sell_price(sells, flow_per_day, wait_tolerance_days):
    """Our sell-listing price, or None if the book side is empty. Mirrored."""
    if not sells or flow_per_day <= 0:
        return None
    max_queue = wait_tolerance_days * flow_per_day
    price, ahead = sells[0][0] - 1, 0
    for prev, nxt in zip(sells, sells[1:]):
        ahead += prev[1]
        if ahead > max_queue:
            break
        price = nxt[0] - 1
    return price


def queue_ahead(levels, price, side):
    """Units queued ahead of an order at `price`: strictly better-priced
    levels only ("buy": higher buys fill first, "sell": cheaper sells)."""
    if side == "buy":
        return sum(q for p, q in levels if p > price)
    return sum(q for p, q in levels if p < price)


def dump_value(buys, qty):
    """Gross proceeds of selling qty units instantly into the buy book.

    Walks best-first. If the book is shallower than qty, the remainder is
    valued at the deepest visible level; optimistic there, but the number
    only serves as a worst-case exit marker.
    """
    if not buys or qty <= 0:
        return 0
    total, left = 0, qty
    for price, lvl_qty in buys:
        take = min(left, lvl_qty)
        total += take * price
        left -= take
        if left == 0:
            return total
    return total + left * buys[-1][0]
