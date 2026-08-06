"""Mark-to-market P&L over a window of completed trading post transactions.

Pure functions over transaction rows as /v2/commerce/transactions/history
returns them: {"item_id", "price", "quantity", "purchased"} with price the
unit price in copper.

Three numbers come out, because "how much did I make" has three honest
answers while stock is still in flight:

- cash        coin that actually moved: sells net of the 15% cut, minus what
              filled buy orders cost. Goes deeply negative during a buying
              spree even when nothing was lost.
- net_floor   cash plus the change in stock held, valued at what it would
              fetch dumped into the current buy book net of fees. The
              liquidation number: what the window is worth if you bail now.
- net_mark    same, but stock is valued at the current sell listing net of
              fees. What the window is worth if every flip completes.

The truth sits between the last two. Neither is "the" account value change:
gold spent from the wallet on a buy order becomes stock of similar worth, so
account value barely moves on a purchase even though cash drops.

Fee convention follows scorers.flip: 15% of the sale, 5% listing plus 10%
tax, charged at sale time. The listing 5% is really charged when the order
is posted, so cash for items listed inside the window but not yet sold is
slightly overstated here.
"""

from features import book
from scorers.flip import TAX


def in_window(rows, since, until=None):
    """Rows whose `purchased` timestamp falls in [since, until).

    Timestamps are ISO-8601 Zulu strings, which sort lexicographically, so
    they are compared as strings and no parsing is needed.
    """
    out = []
    for r in rows:
        t = r.get("purchased")
        if t is None or t < since:
            continue
        if until is not None and t >= until:
            continue
        out.append(r)
    return out


def totals(rows):
    """(coin, {item_id: qty}) summed over rows, coin at the gross unit price."""
    coin, units = 0, {}
    for r in rows:
        qty = r["quantity"]
        coin += r["price"] * qty
        units[r["item_id"]] = units.get(r["item_id"], 0) + qty
    return coin, units


def net_of_fees(gross):
    return int(round(gross * (1 - TAX)))


def dump_mark(books, item_id, qty):
    """Gross coin from dumping qty units into the current buy book."""
    b = books.get(item_id)
    if not b:
        return 0
    return book.dump_value(b["buys"], qty)


def list_mark(books, item_id, qty):
    """Gross coin from selling qty units at the current best sell listing.

    Optimistic on purpose: it assumes the whole lot clears at the top of the
    book without moving it and without being undercut.
    """
    b = books.get(item_id)
    if not b or not b["sells"]:
        return 0
    return b["sells"][0][0] * qty


def stock_value(units, books, mark):
    """Value of a {item_id: qty} holding under `mark`, net of fees."""
    return sum(net_of_fees(mark(books, i, q)) for i, q in units.items())


def compute(buy_rows, sell_rows, books):
    """Window P&L from filled buys and sells plus current order books."""
    spent, bought = totals(buy_rows)
    sold_gross, sold_units = totals(sell_rows)
    received = net_of_fees(sold_gross)
    cash = received - spent

    gained_floor = stock_value(bought, books, dump_mark)
    gained_mark = stock_value(bought, books, list_mark)
    given_floor = stock_value(sold_units, books, dump_mark)
    given_mark = stock_value(sold_units, books, list_mark)

    return {
        "buy_fills": len(buy_rows),
        "sell_fills": len(sell_rows),
        "spent": spent,
        "sold_gross": sold_gross,
        "fees": sold_gross - received,
        "received": received,
        "cash": cash,
        "bought_units": bought,
        "sold_units": sold_units,
        "stock_delta_floor": gained_floor - given_floor,
        "stock_delta_mark": gained_mark - given_mark,
        "net_floor": cash + gained_floor - given_floor,
        "net_mark": cash + gained_mark - given_mark,
    }


def gold(copper):
    """Copper as a signed g/s/c string, the way the game shows it."""
    sign = "-" if copper < 0 else ""
    c = abs(int(copper))
    return f"{sign}{c // 10000}g {c % 10000 // 100:02d}s {c % 100:02d}c"
