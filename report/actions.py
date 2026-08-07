"""Your personal action queue: the public seasonal picks merged with your account.

Usage:
    python -m report.actions                     # fetch the live queue
    python -m report.actions --file queue.json   # read a local queue document

Reads GW2_API_KEY from the environment or .env; needs the account, tradingpost
and inventories scopes. Everything is read-only: the queue comes from the
public report site, the account state from the GW2 API, and nothing is written
anywhere. The key never leaves this machine; keep this command out of CI.

Heavy imports stay inside functions so the module imports with stdlib only.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from account import pnl

ACTIONS_URL = "https://petrcala.github.io/gw2-advisor/actions.json"
NEEDED_SCOPES = {"account", "tradingpost", "inventories"}
BUCKETS = ("buy_now", "opens_soon", "sell_active", "dormant")


class QueueNotLive(RuntimeError):
    pass


def aggregate_orders(rows, ids=None):
    """{item_id: {"qty", "min", "max"}} from current-transaction rows.

    Unit prices in copper: "max" is the strongest bid on the buy side, "min"
    the cheapest listing on the sell side. `ids` restricts to those items.
    """
    aggs = {}
    for r in rows:
        iid = r.get("item_id")
        if iid is None or (ids is not None and iid not in ids):
            continue
        a = aggs.setdefault(iid, {"qty": 0, "min": r["price"], "max": r["price"]})
        a["qty"] += r.get("quantity", 0)
        a["min"] = min(a["min"], r["price"])
        a["max"] = max(a["max"], r["price"])
    return aggs


def personal_queue(queue, held, buys, sells):
    """Merge the public queue with account state; queue order is kept.

    held is {item_id: count}; buys/sells are aggregate_orders() output for the
    open orders on each side. Sell picks with no position collapse to a count;
    buy picks always show (they are the call to action); dormant only counts.
    """
    result = {"sell": [], "buy": [], "soon": [], "dormant": 0,
              "sell_skipped": 0, "total": len(queue)}
    for pick in queue:
        iid = pick.get("id")
        bucket = pick.get("bucket")
        if bucket == "dormant":
            result["dormant"] += 1
        elif bucket == "sell_active":
            agg = sells.get(iid)
            have = held.get(iid, 0)
            listed = agg["qty"] if agg else 0
            if have or listed:
                result["sell"].append({"pick": pick, "held": have, "listed": listed,
                                       "listed_min": agg["min"] if agg else None})
            else:
                result["sell_skipped"] += 1
        elif bucket == "buy_now":
            agg = buys.get(iid)
            limit = pick.get("limit_price")
            bid_max = agg["max"] if agg else None
            gap = None
            if limit is not None and bid_max is not None:
                gap = limit - bid_max
            result["buy"].append({"pick": pick, "held": held.get(iid, 0),
                                  "bid_qty": agg["qty"] if agg else 0,
                                  "bid_max": bid_max, "gap": gap})
        elif bucket == "opens_soon":
            result["soon"].append({"pick": pick})
    return result


def _s(n):
    return "" if n == 1 else "s"


def _age(generated, now):
    try:
        dt = datetime.fromisoformat(generated)
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    hours = ((now or datetime.now(timezone.utc)) - dt).total_seconds() / 3600
    return f", {hours:.0f}h ago" if hours >= 0 else ""


def _closes(date, days_left):
    if not date:
        return ""
    out = f", closes {date}"
    if days_left is not None:
        out += f" ({days_left}d)"
    return out


def _position(held, suggested):
    if not held:
        return "none held"
    out = f"hold {held}"
    if suggested is not None:
        out += f" of {suggested} suggested"
    return out


def _sell_line(rec, g):
    p = rec["pick"]
    out = f"hold {rec['held']}"
    if rec["listed"]:
        out += f", {rec['listed']} listed (from {g(rec['listed_min'])})"
    else:
        out += ", none listed"
    out += _closes(p.get("sell_closes"), p.get("days_left"))
    if rec["held"]:
        out += ", list at market"
    return out


def _buy_line(rec, g):
    p = rec["pick"]
    limit = p.get("limit_price")
    if rec["bid_qty"]:
        out = f"bid {rec['bid_qty']} @ {g(rec['bid_max'])}"
        if rec["gap"] is None:
            if limit is None:
                out += ", no limit set"
        elif rec["gap"] >= 0:
            out += f" ({g(rec['gap'])} under limit {g(limit)})"
        else:
            out += f" ABOVE limit {g(limit)}"
        if rec["held"]:
            out += ", " + _position(rec["held"], p.get("suggested_qty"))
    else:
        out = f"no bid, {_position(rec['held'], p.get('suggested_qty'))}; "
        cur = p.get("cur_price", 0)
        if limit is not None:
            out += f"limit {g(limit)}, now {g(cur)}"
            if limit > 0:
                pct = (cur - limit) / limit * 100
                if pct >= 0.05:
                    out += f" ({pct:.1f}% over)"
                elif pct <= -0.05:
                    out += f" ({-pct:.1f}% under)"
                else:
                    out += " (at limit)"
        else:
            out += f"no limit set, now {g(cur)}"
    return out + _closes(p.get("buy_closes"), p.get("days_left"))


def _soon_line(pick):
    opens = pick.get("buy_opens")
    when = f"buy opens {opens} (in {pick['days_to_buy']}d)" if opens else \
        f"buy opens in {pick['days_to_buy']}d"
    if pick.get("event"):
        when += f", {pick['event']}"
    return when


def render(result, generated=None, now=None):
    """personal_queue() output as a printable string; `now` injectable for tests."""
    g = pnl.gold

    def line(pick, rest):
        return f"  {pick['name'][:40]:<40} {rest}"

    head = "Personal queue vs actions.json"
    if generated:
        head += f" (generated {generated}{_age(generated, now)})"
    out = [head, ""]

    if result["total"] == 0:
        out.append("The queue is empty.")
        return "\n".join(out)

    sell, buy, soon = result["sell"], result["buy"], result["soon"]
    skipped = result["sell_skipped"]
    if not (sell or buy or soon):
        out.append("Nothing actionable: nothing held or listed for the sell "
                   "picks, no buy window open.")
    if sell:
        out.append("SELL - sell window active")
        out += [line(r["pick"], _sell_line(r, g)) for r in sell]
        if skipped:
            out.append(f"  ({skipped} more sell pick{_s(skipped)}, nothing held or listed)")
        out.append("")
    elif skipped and (buy or soon):
        out += [f"{skipped} sell window pick{_s(skipped)}, nothing held or listed.", ""]
    if buy:
        out.append("BUY - buy window open")
        out += [line(r["pick"], _buy_line(r, g)) for r in buy]
        out.append("")
    if soon:
        out.append("OPENS SOON")
        out += [line(r["pick"], _soon_line(r["pick"])) for r in soon]
        out.append("")
    if result["dormant"]:
        out.append(f"{result['dormant']} dormant pick{_s(result['dormant'])} skipped.")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def fetch_queue(url=ACTIONS_URL):
    """GET the public queue document; 404 means it has not deployed yet."""
    import requests

    resp = requests.get(url, timeout=30)
    if resp.status_code == 404:
        raise QueueNotLive(url)
    resp.raise_for_status()
    return resp.json()


def load_queue(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def account_state(ids):
    """(held, buy_aggs, sell_aggs) for `ids`: holdings and open orders.

    Holdings cover bank, material storage and shared inventory (BULK_SLOTS),
    not character bags; open orders come fully paged from the commerce API.
    """
    from account import gw2account, snapshot

    acct = gw2account.Account()
    scopes = set(acct.token_info().get("permissions", []))
    missing = NEEDED_SCOPES - scopes
    if missing:
        raise PermissionError(
            f"key is missing the {', '.join(sorted(missing))} scope(s); it has "
            f"{', '.join(sorted(scopes)) or 'none'}. The personal queue needs "
            f"{', '.join(sorted(NEEDED_SCOPES))}."
        )
    held = {}
    for path in snapshot.BULK_SLOTS:
        for slot in acct.get(path) or []:
            if not slot or slot.get("id") not in ids:
                continue
            if slot.get("count", 0) > 0:
                held[slot["id"]] = held.get(slot["id"], 0) + slot["count"]
    buys = aggregate_orders(acct.current_orders("buys"), ids)
    sells = aggregate_orders(acct.current_orders("sells"), ids)
    return held, buys, sells


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--file", help="read the queue from a local JSON file "
                                   "instead of the public URL")
    args = ap.parse_args()

    from account import gw2account
    from account.window import load_dotenv
    import requests

    load_dotenv()

    if args.file:
        doc = load_queue(args.file)
    else:
        try:
            doc = fetch_queue()
        except QueueNotLive:
            print(f"error: the action queue is not live yet at {ACTIONS_URL} "
                  "(issue #16 has not deployed). Retry after the next report "
                  "build, or point --file at a local queue document.",
                  file=sys.stderr)
            return 1
        except requests.RequestException as e:
            print(f"error: could not fetch {ACTIONS_URL}: {e}", file=sys.stderr)
            return 1

    queue = doc.get("queue") or []
    ids = {r["id"] for r in queue if r.get("bucket") != "dormant"}
    try:
        held, buys, sells = account_state(ids) if ids else ({}, {}, {})
    except (gw2account.MissingKey, PermissionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(render(personal_queue(queue, held, buys, sells),
                 generated=doc.get("generated")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
