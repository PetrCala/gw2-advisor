"""Value the whole account and store the number, so later windows have a baseline.

Usage:
    python -m account.snapshot              # take one, compare to the previous
    python -m account.snapshot --list
    python -m account.snapshot --compare    # compare the two newest, take none

Snapshots land in .snapshots/ as account-<timestamp>.json, git-ignored.

Nothing reconstructs the past here: the first snapshot only establishes a
baseline, and a delta needs two. Run it on a schedule if you want an account
value history; the GW2 API has no such history of its own.

Holdings are valued two ways, both net of the 15% cut:
  floor  at the current best buy order, what an instant dump fetches
  mark   at the current best sell listing, what patient selling fetches
Neither uses order-book depth: a full account touches thousands of items and
/v2/commerce/prices covers them in a fraction of the requests that
/v2/commerce/listings would need. Per-item depth stays in account.window,
which only prices what you actually traded.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from account import gw2account, pnl
from account.window import load_dotenv
from collector import gw2api

ROOT = Path(__file__).resolve().parent.parent
SNAP_DIR = ROOT / ".snapshots"

# Bound the account endpoints we walk. Bank, materials and shared inventory
# are one call each; character bags are one call per character.
BULK_SLOTS = ["/account/bank", "/account/materials", "/account/inventory"]


def held_items(acct):
    """{item_id: count} across bank, materials, shared slots and character bags."""
    held = {}

    def add(rows):
        for slot in rows or []:
            if not slot or not slot.get("id"):
                continue
            held[slot["id"]] = held.get(slot["id"], 0) + slot.get("count", 0)

    for path in BULK_SLOTS:
        add(acct.get(path))
    for name in acct.get("/characters"):
        bags = acct.get(f"/characters/{requests.utils.quote(name)}/inventory")
        for bag in bags.get("bags") or []:
            if bag:
                add(bag.get("inventory"))
    return held


def value(held, prices):
    """(floor, mark) copper for a holding, both net of fees."""
    floor = mark = 0
    for item_id, count in held.items():
        p = prices.get(item_id)
        if not p:
            continue
        buy, sell = p[0], p[2]
        floor += pnl.net_of_fees(buy * count)
        mark += pnl.net_of_fees(sell * count)
    return floor, mark


def take(acct=None):
    acct = acct or gw2account.Account()
    scopes = set(acct.token_info().get("permissions", []))
    need = {"account", "wallet", "inventories", "characters", "tradingpost"}
    missing = need - scopes
    if missing:
        raise PermissionError(
            f"key is missing the {', '.join(sorted(missing))} scope(s); a full "
            "account valuation needs all of "
            f"{', '.join(sorted(need))}"
        )

    coins = acct.coins()
    delivery = acct.delivery()
    held = held_items(acct)
    for it in delivery.get("items", []):
        held[it["id"]] = held.get(it["id"], 0) + it["count"]

    open_buys = acct.current_orders("buys")
    open_sells = acct.current_orders("sells")
    buy_capital = sum(o["price"] * o["quantity"] for o in open_buys)
    for o in open_sells:
        held[o["item_id"]] = held.get(o["item_id"], 0) + o["quantity"]

    prices = gw2api.fetch_all_prices()
    floor, mark = value(held, prices)
    liquid = coins + delivery.get("coins", 0) + buy_capital

    return {
        "taken_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coins": coins,
        "delivery_coins": delivery.get("coins", 0),
        "open_buy_capital": buy_capital,
        "open_buy_orders": len(open_buys),
        "open_sell_orders": len(open_sells),
        "distinct_items": len(held),
        "stock_floor": floor,
        "stock_mark": mark,
        "total_floor": liquid + floor,
        "total_mark": liquid + mark,
    }


def store(snap):
    SNAP_DIR.mkdir(exist_ok=True)
    path = SNAP_DIR / f"account-{snap['taken_at'].replace(':', '')}.json"
    path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    return path


def existing():
    if not SNAP_DIR.exists():
        return []
    return sorted(SNAP_DIR.glob("account-*.json"))


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def render(snap, prev=None):
    g = pnl.gold
    out = [
        f"Account snapshot {snap['taken_at']}",
        f"  wallet                      {g(snap['coins']):>18}",
        f"  delivery box                {g(snap['delivery_coins']):>18}",
        f"  capital in open buy orders  {g(snap['open_buy_capital']):>18}",
        f"  stock, {snap['distinct_items']:>5} distinct items",
        f"    at liquidation            {g(snap['stock_floor']):>18}",
        f"    at sell listings          {g(snap['stock_mark']):>18}",
        "",
        f"  TOTAL   {g(snap['total_floor'])} to {g(snap['total_mark'])}",
    ]
    if prev:
        t0 = datetime.strptime(prev["taken_at"], "%Y-%m-%dT%H:%M:%SZ")
        t1 = datetime.strptime(snap["taken_at"], "%Y-%m-%dT%H:%M:%SZ")
        hours = (t1 - t0).total_seconds() / 3600
        out += [
            "",
            f"Change over {hours:.1f}h since {prev['taken_at']}",
            f"  at liquidation            "
            f"{g(snap['total_floor'] - prev['total_floor']):>18}",
            f"  at sell listings          "
            f"{g(snap['total_mark'] - prev['total_mark']):>18}",
        ]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="list stored snapshots")
    ap.add_argument("--compare", action="store_true", help="diff the two newest, take none")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    load_dotenv()
    have = existing()

    if args.list:
        for p in have:
            s = load(p)
            print(f"{s['taken_at']}  {pnl.gold(s['total_floor'])} to {pnl.gold(s['total_mark'])}")
        if not have:
            print("no snapshots yet")
        return 0

    if args.compare:
        if len(have) < 2:
            print(f"need two snapshots to compare, have {len(have)}", file=sys.stderr)
            return 2
        print(render(load(have[-1]), load(have[-2])))
        return 0

    try:
        snap = take()
    except (gw2account.MissingKey, PermissionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    path = store(snap)
    prev = load(have[-1]) if have else None
    print(json.dumps(snap, indent=2) if args.json else render(snap, prev))
    if not prev:
        print("\nFirst snapshot: this is the baseline. Deltas start from the next run.")
    print(f"\nstored {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
