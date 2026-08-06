"""What your trading did over the last N hours.

Usage:
    python -m account.window                # last 5 hours
    python -m account.window --hours 12
    python -m account.window --hours 5 --json

Reads GW2_API_KEY from the environment or .env. Needs the account and
tradingpost scopes; wallet is used for context when granted.

This reports trading activity, not total account value: the GW2 API exposes
only current state, and no public API serves your account value as it stood
N hours ago, so a value delta over a past window cannot be reconstructed.
Run `python -m account.snapshot` to start a baseline for future windows.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from account import gw2account, pnl
from collector import gw2api

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path=ROOT / ".env"):
    """Minimal KEY=VALUE loader; existing environment wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def item_names(ids, session=None):
    s = session or requests.Session()
    names, ids = {}, list(ids)
    for i in range(0, len(ids), 200):
        chunk = ids[i : i + 200]
        rows = gw2api._get_json(
            s, "https://api.guildwars2.com/v2/items", {"ids": ",".join(map(str, chunk))}
        )
        for r in rows:
            names[r["id"]] = r.get("name", str(r["id"]))
    return names


def collect(hours):
    acct = gw2account.Account()
    info = acct.token_info()
    scopes = set(info.get("permissions", []))
    missing = {"account", "tradingpost"} - scopes
    if missing:
        raise PermissionError(
            f"key is missing the {', '.join(sorted(missing))} scope(s); "
            f"it has {', '.join(sorted(scopes)) or 'none'}"
        )

    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    raw_buys, buys_cut = acct.history("buys", since=since)
    raw_sells, sells_cut = acct.history("sells", since=since)
    buys = pnl.in_window(raw_buys, since)
    sells = pnl.in_window(raw_sells, since)

    ids = {r["item_id"] for r in buys} | {r["item_id"] for r in sells}
    books = gw2api.fetch_listings(ids) if ids else {}
    result = pnl.compute(buys, sells, books)
    result["since"] = since
    result["until"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    result["hours"] = hours
    result["truncated"] = buys_cut or sells_cut
    result["names"] = item_names(ids) if ids else {}

    if "wallet" in scopes:
        result["wallet_coins"] = acct.coins()
    delivery = acct.delivery()
    result["delivery_coins"] = delivery.get("coins", 0)
    result["delivery_items"] = sum(i["count"] for i in delivery.get("items", []))
    result["open_buy_capital"] = sum(
        o["price"] * o["quantity"] for o in acct.current_orders("buys")
    )
    result["open_sell_gross"] = sum(
        o["price"] * o["quantity"] for o in acct.current_orders("sells")
    )
    return result


def render(r):
    g = pnl.gold
    out = [
        f"Trading window: last {r['hours']}h ({r['since'][:19]}Z to {r['until'][:19]}Z)",
        "",
        f"  {r['buy_fills']:>4} buy fills             {g(-r['spent']):>18}",
        f"  {r['sell_fills']:>4} sell fills            {g(r['sold_gross']):>18} gross",
        f"       trading post cut       {g(-r['fees']):>18}",
        f"       net from sells         {g(r['received']):>18}",
        "",
        f"  Cash moved                  {g(r['cash']):>18}",
        f"  Stock delta (liquidation)   {g(r['stock_delta_floor']):>18}",
        f"  Stock delta (if flips land) {g(r['stock_delta_mark']):>18}",
        "",
        f"  Window value change         {g(r['net_floor'])} to {g(r['net_mark'])}",
        "",
        "Current state:",
    ]
    if "wallet_coins" in r:
        out.append(f"  wallet                      {g(r['wallet_coins']):>18}")
    out += [
        f"  delivery box                {g(r['delivery_coins']):>18}"
        f"  + {r['delivery_items']} items",
        f"  capital in open buy orders  {g(r['open_buy_capital']):>18}",
        f"  open sell listings (gross)  {g(r['open_sell_gross']):>18}",
    ]

    moved = sorted(
        set(r["bought_units"]) | set(r["sold_units"]),
        key=lambda i: -(r["bought_units"].get(i, 0) + r["sold_units"].get(i, 0)),
    )
    if moved:
        out += ["", "Items traded:"]
        for i in moved[:25]:
            name = r["names"].get(i, r["names"].get(str(i), str(i)))
            b, s = r["bought_units"].get(i, 0), r["sold_units"].get(i, 0)
            out.append(f"  {name[:40]:<40} bought {b:>5}   sold {s:>5}")
        if len(moved) > 25:
            out.append(f"  ... and {len(moved) - 25} more")

    if r["truncated"]:
        out += ["", "WARNING: hit the page cap, the window is only partly covered."]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=5.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    load_dotenv()
    try:
        result = collect(args.hours)
    except (gw2account.MissingKey, PermissionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
