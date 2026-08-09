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
show class exposure against the allocation caps, and each class also
carries its structural floor (invest.bounds, S7), which is the aggregate
half of risk rule 7: what share of the price net of tax is underwritten by
a vendor price or a conversion route rather than by sentiment. That series
cannot be backfilled - no record of past holdings or past vendor values
exists - so it is a from-today-forward number, the same honest posture as
the radar's documented burn-in.

The structural leg is optional and degrades rather than failing: it needs
one extra dw2 snapshot call, and this series is the one thing in the repo
that genuinely cannot be recovered later, so a datawars2 timeout must cost
today's coverage number and never today's point.

The series lives in the private bucket with absolute gold; the public page
renders it rebased to 100 (report.invest), so wealth stays off the site.
"""

import os
import sys
from datetime import datetime, timezone

from account import pnl
from invest import bounds, tags

NAV_KEY = "invest/nav.json.gz"
MAX_POINTS = 2000  # about five and a half years of daily points


def build_point(gathered, prices, today, floors=None):
    """One day's NAV: totals at liquidation and mark, stock by class.

    `floors` is {item_id: structural floor per unit in copper}
    (invest.bounds.structural_floor, S7). Given it, the point also carries
    structural_classes, mark_classes, stock_structural and structural_items,
    the aggregate half of risk rule 7: how much of the price net of tax is
    structurally underwritten, per class and in total. Left None, the point
    is byte-identical to every point stored before this milestone, so old
    series and existing callers keep working untouched.

    Naming, deliberately: `floor`/`stock_floor`/`classes` are the LIQUIDATION
    totals this series has always carried (a dump into the bid net of fees),
    and everything with "structural" in its name is S7's bound. The two are
    never the same quantity, so no key spans both words.

    The structural coverage denominator is `mark_classes`, the ask net of
    tax, which is what docs/INVESTING.md risk rule 7 and
    invest.bounds.floor_coverage both measure a floor against. Getting that
    denominator wrong is not cosmetic: on the wide-spread items S4 targets,
    bid-net and ask-net differ by the whole spread, and the same position
    would then read two different coverages on two pages of one site.

    `structural_classes` carries only the classes a floor was actually
    computable for. A class that is missing is unmeasured, which is a
    different statement from a floor of zero, and the page renders the two
    differently.
    """
    classes = dict.fromkeys(tags.CLASSES, 0)
    mark_classes = dict.fromkeys(tags.CLASSES, 0)
    structural_classes = {}
    floor = mark = structural = 0
    unpriced = structural_items = 0
    for item_id, count in gathered["held"].items():
        p = prices.get(item_id)
        if not p:
            unpriced += 1
            continue
        buy, sell = p[0], p[2]
        item_floor = pnl.net_of_fees(buy * count)
        item_mark = pnl.net_of_fees(sell * count)
        floor += item_floor
        mark += item_mark
        cls = tags.classify(item_id, sell)
        classes[cls] += item_floor
        mark_classes[cls] += item_mark
        if floors:
            per_unit = floors.get(item_id)
            if per_unit:
                structural_classes[cls] = structural_classes.get(cls, 0) + per_unit * count
                structural += per_unit * count
                structural_items += 1
    liquid = (
        gathered["coins"] + gathered["delivery_coins"] + gathered["open_buy_capital"]
    )
    point = {
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
    if floors is not None:
        point["structural_classes"] = structural_classes
        point["mark_classes"] = mark_classes
        point["stock_structural"] = structural
        point["structural_items"] = structural_items
    return point


def append(series, point, cap=MAX_POINTS):
    """The series with today's point added, one per day, oldest dropped."""
    points = [p for p in (series.get("points") or []) if p.get("d") != point["d"]]
    points.append(point)
    points.sort(key=lambda p: p["d"])
    return {"points": points[-cap:]}


def _structural_floors(held_ids, prices):
    """{item_id: floor per unit} for the held ids that have one (S7), or
    None when the snapshot the floors are read from could not be fetched.

    Costs one extra dw2 snapshot request (vendor_value already rides in the
    default fields, no widening needed) and one archive parse; the
    fetch_all_prices call main() already makes supplies the conversion
    leg's price map for free.

    None rather than {} on failure, because the two mean different things
    downstream: {} is "measured, nothing had a floor" and None is "not
    measured today", which build_point renders as a legacy point.
    """
    from collector import dw2

    try:
        snap = dw2.fetch_snapshot()
    except Exception as e:  # noqa: BLE001 - an optional leg must never cost the point
        print(f"structural floors skipped: {type(e).__name__}: {e}", file=sys.stderr)
        return None

    by_id = {it["id"]: it for it in snap if it.get("id") is not None}
    graph = bounds.load_graph()
    out = {}
    for item_id in held_ids:
        row = by_id.get(item_id)
        if not row:
            continue
        f = bounds.structural_floor(item_id, row, prices, graph)["floor"]
        if f:
            out[item_id] = f
    return out


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
    floors = _structural_floors(gathered["held"], prices)
    today = datetime.now(timezone.utc).date()
    point = build_point(gathered, prices, today, floors)

    store = Store(os.environ["S3_BUCKET"])
    series = append(store.get_json_gz(NAV_KEY) or {}, point)
    store.put_json_gz(NAV_KEY, series)
    # This repo is public, so its Actions logs are too: never print absolute
    # gold here, only shape. The report page is where the owner reads value,
    # rebased to 100 (see report.invest).
    mark_premium = point["mark"] / point["floor"] - 1 if point["floor"] else 0.0
    # "floor" on its own is the word this milestone stopped using: the two
    # meanings would otherwise sit eight words apart in one public log line.
    tail = ""
    if floors is not None:
        stock_mark = sum(point["mark_classes"].values())
        covered = point["stock_structural"] / stock_mark if stock_mark else 0.0
        tail = (
            f", structural floors cover {covered:.1%} of stock "
            f"across {point['structural_items']} items"
        )
    print(
        f"NAV {point['d']}: {point['items']} items ({point['unpriced']} unpriced), "
        f"mark {mark_premium:+.1%} above liquidation{tail} "
        f"({len(series['points'])} points) -> s3://{store.bucket}/{NAV_KEY}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
