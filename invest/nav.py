"""Daily portfolio NAV point, appended to invest/nav.json.gz.

Usage:
    python -m invest.nav    # needs S3_BUCKET and GW2_API_KEY

Runs in the daily report workflow before the page build; without a
GW2_API_KEY it skips cleanly, so the report never depends on the key
existing. One point per UTC day; a rerun the same day replaces the point.

Valuation reuses account.snapshot's gathering (wallet, delivery box, bank,
materials, character bags, open orders) and the same fee convention. The
headline NAV is the floor: everything dumped into the current best bid net
of the 15% cut, per risk rule 6 in docs/INVESTING.md (optimism lives in
theses, never in accounting); the patient sell-listing mark rides along.
Stock is broken down by material class (invest.tags) so the report can
show class exposure against the allocation caps.

The series lives in the private bucket with absolute gold; the public page
renders it rebased to 100 (report.invest), so wealth stays off the site.
"""

import os
import sys
from datetime import datetime, timezone

from account import pnl
from invest import tags

NAV_KEY = "invest/nav.json.gz"
MAX_POINTS = 2000  # about five and a half years of daily points


def build_point(gathered, prices, today):
    """One day's NAV: totals at floor and mark, stock by class at floor."""
    classes = dict.fromkeys(tags.CLASSES, 0)
    floor = mark = 0
    unpriced = 0
    for item_id, count in gathered["held"].items():
        p = prices.get(item_id)
        if not p:
            unpriced += 1
            continue
        buy, sell = p[0], p[2]
        item_floor = pnl.net_of_fees(buy * count)
        floor += item_floor
        mark += pnl.net_of_fees(sell * count)
        classes[tags.classify(item_id, sell)] += item_floor
    liquid = (
        gathered["coins"] + gathered["delivery_coins"] + gathered["open_buy_capital"]
    )
    return {
        "d": today.isoformat(),
        "floor": liquid + floor,
        "mark": liquid + mark,
        "liquid": liquid,
        "coins": gathered["coins"],
        "buy_escrow": gathered["open_buy_capital"],
        "stock_floor": floor,
        "items": len(gathered["held"]),
        "unpriced": unpriced,
        "classes": classes,
    }


def append(series, point, cap=MAX_POINTS):
    """The series with today's point added, one per day, oldest dropped."""
    points = [p for p in (series.get("points") or []) if p.get("d") != point["d"]]
    points.append(point)
    points.sort(key=lambda p: p["d"])
    return {"points": points[-cap:]}


def main():
    from account import gw2account, snapshot
    from account.window import load_dotenv
    from collector import gw2api
    from collector.s3store import Store

    load_dotenv()
    try:
        acct = gw2account.Account()
    except gw2account.MissingKey:
        print("GW2_API_KEY is not set; NAV point skipped")
        return 0
    try:
        gathered = snapshot.gather(acct)
    except PermissionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    prices = gw2api.fetch_all_prices()
    today = datetime.now(timezone.utc).date()
    point = build_point(gathered, prices, today)

    store = Store(os.environ["S3_BUCKET"])
    series = append(store.get_json_gz(NAV_KEY) or {}, point)
    store.put_json_gz(NAV_KEY, series)
    print(
        f"NAV {point['d']}: floor {pnl.gold(point['floor'])}, "
        f"mark {pnl.gold(point['mark'])}, {point['items']} items "
        f"({len(series['points'])} points) -> s3://{store.bucket}/{NAV_KEY}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
