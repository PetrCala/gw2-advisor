"""Paper-trading tracker: score each day's picks against what the market did.

The report's numbers are model output. This module turns them into a claim
that can be checked. Every day it snapshots the flip picks and the seasonal
buy-now queue as paper positions, then replays them against the following
days' snapshots and books what the round trip would actually have netted.

The simulation, stated plainly:

- A flip buy order fills only on days the market's best bid has come down to
  our price (bid <= buy_at, so our order is at the front of its queue); on
  those days we take CAPTURE of that day's filled buy flow. The sell leg
  mirrors it (ask >= sell_at, CAPTURE of the day's sell flow). Whatever is
  still held when the position ages out is marked at the last observed bid,
  net of fees, so every position resolves to a number.
- A seasonal pick buys off listings on the days the ask sits under its
  pay-up-to ceiling, and is marked out over the prices observed inside its
  sell window.

What that measures, and what it cannot. Fills are simulated, so this is not
a measurement of our own share of the flow: nothing short of trading real
gold can produce that. It measures the two things that are observable, and
both are the ones that go wrong. First, whether our placement ever reached
the front of the book at all: an order that never gets there never fills,
which is exactly the failure the unbounded gap walk (PR #29) produced and a
human caught by eye. Second, how much flow traded while it was there, from
which comes "capture needed": the share of that flow we would have had to
absorb to finish the lot inside the predicted round trip. A median capture
needed far above CAPTURE says the assumed 25% is optimistic.

Daily sampling bounds the resolution: a two-day predicted round trip gets
two observations, so timings are coarse by construction, and a fill that
took an hour and one that took a day both read as one day.

State lives in S3 under track/, bounded by position and record caps, next
to the notify state and under the same never-gate-the-deploy rule.

Usage:
    python -m report.track    # needs S3_BUCKET; runs after report.build

It reads the freshly built site/data.json and site/actions.json and costs one
extra datawars2 snapshot request a day, the same one the build makes.
"""

import json
import math
import os
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from statistics import median

from scorers import flip

SITE = Path(__file__).resolve().parent.parent / "site"

OPEN_KEY = "track/open.json.gz"
CLOSED_KEY = "track/closed.json.gz"

TAX = flip.TAX
CAPTURE = flip.CAPTURE  # the assumption under test, read from the scorer itself

FLIP_HORIZON_DAYS = 7  # a 2-day predicted round trip gets a week to happen
SEASON_HORIZON_DAYS = 400  # one full cycle plus slack, then mark it out
FLIP_PER_DAY = flip.TOP_N  # the picks as published, no wider
OPEN_CAP = 800  # backstop on state size; the horizons bound it well below this
CLOSED_CAP = 400
TABLE_ROWS = 40  # rows rendered per table; the json carries everything
EPS = 1e-9

# Open positions carry running aggregates, so a field added later would make
# the stored ones unreadable. They are stamped instead, and a stamp we no
# longer understand is dropped on load: the tracker loses a few days of open
# positions rather than crashing on every run from then on.
SCHEMA = 1

REPORT_URL = "index.html"


def _f(v):
    """Snapshot numbers arrive as int, float, None or missing."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _days(opened, today):
    return (today - date.fromisoformat(opened)).days


# --- opening -------------------------------------------------------------


def open_flip(pick, today):
    """A flip pick as a paper position, with the prediction it has to beat."""
    return {
        "v": SCHEMA,
        "kind": "flip",
        "id": pick["id"],
        "name": pick.get("name") or f"item {pick['id']}",
        "rarity": pick.get("rarity") or "",
        "opened": today.isoformat(),
        "buy_at": pick["buy_at"],
        "sell_at": pick["sell_at"],
        "qty": pick["qty"],
        "margin": pick["margin"],
        "pred_rt_days": pick["round_trip_days"],
        # capture needed is only meaningful over the horizon the model
        # predicted, so flow seen past it is counted but not charged.
        "horizon_days": max(1, math.ceil(pick["round_trip_days"])),
        "days": 0,
        "filled": 0.0,
        "sold": 0.0,
        "spent": 0.0,
        "revenue": 0.0,
        "buy_front_days": 0,
        "sell_front_days": 0,
        "buy_flow_window": 0.0,
        "filled_day": None,
        "sold_day": None,
        "last_bid": None,
        "last_ask": None,
        "last_tick": None,
    }


def open_season(row, today):
    """A buy-now queue row as a paper hold; None when it is not actionable."""
    if not row.get("limit_price") or not row.get("suggested_qty"):
        return None
    if not all(row.get(k) for k in ("buy_closes", "sell_opens", "sell_closes")):
        return None
    return {
        "v": SCHEMA,
        "kind": "season",
        "id": row["id"],
        "name": row.get("name") or f"item {row['id']}",
        "event": row.get("event") or "",
        "opened": today.isoformat(),
        "limit_price": row["limit_price"],
        "qty": row["suggested_qty"],
        "entry_price": row.get("cur_price"),
        "buy_closes": row["buy_closes"],
        "sell_opens": row["sell_opens"],
        "sell_closes": row["sell_closes"],
        "pred_ret": row.get("med_ret"),
        "days": 0,
        "fill_days": 0,
        "filled": 0.0,
        "spent": 0.0,
        "window_days": 0,
        "window_sum": 0.0,
        "last_bid": None,
        "last_ask": None,
        "last_tick": None,
    }


# --- daily observation ---------------------------------------------------


def tick_flip(pos, row, today):
    """Advance one flip position by a day of market data."""
    bid, ask = row.get("buy_price"), row.get("sell_price")
    pos["days"] += 1
    if bid:
        pos["last_bid"] = bid
    if ask:
        pos["last_ask"] = ask
    in_window = pos["days"] <= pos["horizon_days"]

    if pos["filled"] < pos["qty"] - EPS and bid and bid <= pos["buy_at"]:
        flow = _f(row.get("1d_buy_sold"))
        pos["buy_front_days"] += 1
        if in_window:
            pos["buy_flow_window"] += flow
        units = min(pos["qty"] - pos["filled"], CAPTURE * flow)
        pos["filled"] += units
        pos["spent"] += units * pos["buy_at"]
        if pos["filled"] >= pos["qty"] - EPS and pos["filled_day"] is None:
            pos["filled_day"] = pos["days"]

    if pos["sold"] < pos["filled"] - EPS and ask and ask >= pos["sell_at"]:
        flow = _f(row.get("1d_sell_sold"))
        pos["sell_front_days"] += 1
        units = min(pos["filled"] - pos["sold"], CAPTURE * flow)
        pos["sold"] += units
        # Same net-of-fee convention as scorers.flip.rescore, so realized and
        # predicted differ in fills and timing only, not in accounting.
        pos["revenue"] += units * (pos["sell_at"] - 1) * (1 - TAX)
        if pos["sold"] >= pos["qty"] - EPS and pos["sold_day"] is None:
            pos["sold_day"] = pos["days"]


def tick_season(pos, row, today):
    """Advance one seasonal hold by a day of market data."""
    bid, ask = row.get("buy_price"), row.get("sell_price")
    pos["days"] += 1
    if bid:
        pos["last_bid"] = bid
    if ask:
        pos["last_ask"] = ask
    iso = today.isoformat()

    buying = pos["filled"] < pos["qty"] - EPS and iso <= pos["buy_closes"]
    if buying and ask and ask <= pos["limit_price"]:
        # Buying off listings is what "pay up to" means: the ceiling is a
        # limit, the ask is what the units actually cost.
        units = min(pos["qty"] - pos["filled"], CAPTURE * _f(row.get("1d_sell_sold")))
        pos["fill_days"] += 1
        pos["filled"] += units
        pos["spent"] += units * ask

    if ask and pos["sell_opens"] <= iso <= pos["sell_closes"]:
        pos["window_days"] += 1
        pos["window_sum"] += ask


TICKS = {"flip": tick_flip, "season": tick_season}


# --- resolution ----------------------------------------------------------


def is_done(pos, today):
    """A flip resolves on a finished round trip or at its horizon; a hold
    when its sell window has passed, or early once its entry is gone."""
    iso = today.isoformat()
    age = _days(pos["opened"], today)
    if pos["kind"] == "flip":
        return pos["sold"] >= pos["qty"] - EPS or age >= FLIP_HORIZON_DAYS
    if iso > pos["sell_closes"] or age >= SEASON_HORIZON_DAYS:
        return True
    return pos["filled"] <= EPS and iso > pos["buy_closes"]


def close_flip(pos, today):
    """Book the position: realized net against what the pick promised.

    Units bought but not sold are marked at the last observed bid net of
    fees, the same instant-exit floor the report shows as exit %, so a
    position that filled and then sat is not scored as a free option.
    """
    held = max(0.0, pos["filled"] - pos["sold"])
    mark = pos["last_bid"] or pos["buy_at"]
    residual = held * mark * (1 - TAX)
    net = pos["revenue"] + residual - pos["spent"]
    pred_net = pos["margin"] * pos["qty"]
    if pos["sold"] >= pos["qty"] - EPS:
        outcome = "closed"
    elif pos["filled"] > EPS:
        outcome = "partial"
    else:
        outcome = "unfilled"
    return {
        "kind": "flip",
        "id": pos["id"],
        "name": pos["name"],
        "rarity": pos["rarity"],
        "opened": pos["opened"],
        "closed": today.isoformat(),
        "outcome": outcome,
        "qty": pos["qty"],
        "filled": round(pos["filled"], 1),
        "sold": round(pos["sold"], 1),
        "days": pos["days"],
        "buy_front_days": pos["buy_front_days"],
        "sell_front_days": pos["sell_front_days"],
        "pred_rt_days": round(pos["pred_rt_days"], 2),
        "rt_days": pos["sold_day"],
        "fill_days": pos["filled_day"],
        "pred_net": round(pred_net, 1),
        "net": round(net, 1),
        "net_pct": round(net / pred_net, 3) if pred_net > 0 else None,
        "capture_needed": (
            round(pos["qty"] / pos["buy_flow_window"], 3)
            if pos["buy_flow_window"] > 0
            else None
        ),
    }


def close_season(pos, today):
    """Book the hold: realized cycle return against the predicted median.

    A hold that reached its sell window is marked at the mean price observed
    inside it; one that aged out first is marked at the last ask and labelled
    as such, since an unfinished cycle is not evidence either way.
    """
    avg_cost = pos["spent"] / pos["filled"] if pos["filled"] > EPS else None
    if pos["window_days"]:
        exit_price = pos["window_sum"] / pos["window_days"]
        outcome = "closed"
    else:
        exit_price = pos["last_ask"]
        outcome = "marked"
    if pos["filled"] <= EPS:
        outcome = "unfilled"
    ret = None
    if avg_cost and exit_price:
        ret = round((exit_price * (1 - TAX) - avg_cost) / avg_cost, 3)
    return {
        "kind": "season",
        "id": pos["id"],
        "name": pos["name"],
        "event": pos["event"],
        "opened": pos["opened"],
        "closed": today.isoformat(),
        "outcome": outcome,
        "qty": pos["qty"],
        "filled": round(pos["filled"], 1),
        "fill_days": pos["fill_days"],
        "days": pos["days"],
        "avg_cost": round(avg_cost) if avg_cost else None,
        "exit_price": round(exit_price) if exit_price else None,
        "window_days": pos["window_days"],
        "ret": ret,
        "pred_ret": pos["pred_ret"],
    }


CLOSERS = {"flip": close_flip, "season": close_season}


# --- the daily roll ------------------------------------------------------


def roll(state, picks, queue, by_id, today):
    """One day of tracking: observe, resolve, then open today's picks.

    Positions are keyed by (kind, item): an item already being tracked is
    not opened again until its position resolves, so the record is a set of
    independent samples rather than the same pick counted seven times.
    """
    iso = today.isoformat()
    positions = [p for p in (state.get("positions") or []) if p.get("v") == SCHEMA]
    records = list(state.get("records") or [])
    since = state.get("since") or iso

    live, closed_now = [], []
    for pos in positions:
        row = by_id.get(pos["id"])
        if row is not None and pos.get("last_tick") != iso:
            TICKS[pos["kind"]](pos, row, today)
            pos["last_tick"] = iso
        if is_done(pos, today):
            closed_now.append(CLOSERS[pos["kind"]](pos, today))
        else:
            live.append(pos)
    records += closed_now

    keys = {(p["kind"], p["id"]) for p in live}
    opened = 0
    for p in picks[:FLIP_PER_DAY]:
        if ("flip", p["id"]) in keys:
            continue
        live.append(open_flip(p, today))
        keys.add(("flip", p["id"]))
        opened += 1
    for r in queue:
        if r.get("bucket") != "buy_now" or ("season", r["id"]) in keys:
            continue
        pos = open_season(r, today)
        if pos:
            live.append(pos)
            keys.add(("season", r["id"]))
            opened += 1

    dropped = max(0, len(live) - OPEN_CAP)
    return (
        {"since": since, "positions": live[dropped:], "records": records[-CLOSED_CAP:]},
        {"opened": opened, "closed": len(closed_now), "dropped": dropped},
    )


def _med(vals):
    vals = [v for v in vals if v is not None]
    return round(median(vals), 3) if vals else None


def summarize(state):
    """Headline numbers over the resolved records and the open book."""
    records = state.get("records") or []
    positions = state.get("positions") or []
    flips = [r for r in records if r["kind"] == "flip"]
    seasons = [r for r in records if r["kind"] == "season"]
    n = len(flips)
    completed = [r for r in flips if r["outcome"] == "closed"]
    scored = [r for r in seasons if r["outcome"] == "closed" and r["ret"] is not None]
    pred_net = sum(r["pred_net"] for r in flips)
    return {
        "since": state.get("since"),
        "open": {
            "flip": sum(1 for p in positions if p["kind"] == "flip"),
            "season": sum(1 for p in positions if p["kind"] == "season"),
        },
        "flip": {
            "n": n,
            "completed": len(completed),
            "partial": sum(1 for r in flips if r["outcome"] == "partial"),
            "unfilled": sum(1 for r in flips if r["outcome"] == "unfilled"),
            "never_front": sum(1 for r in flips if not r["buy_front_days"]),
            "completed_pct": round(len(completed) / n, 3) if n else None,
            "pred_net": round(pred_net),
            "net": round(sum(r["net"] for r in flips)),
            "net_pct": round(sum(r["net"] for r in flips) / pred_net, 3)
            if pred_net > 0
            else None,
            "med_net_pct": _med([r["net_pct"] for r in flips]),
            "med_rt_days": _med([r["rt_days"] for r in completed]),
            "med_pred_rt_days": _med([r["pred_rt_days"] for r in completed]),
            "med_capture_needed": _med([r["capture_needed"] for r in flips]),
            "capture_n": sum(1 for r in flips if r["capture_needed"] is not None),
            "capture_assumed": CAPTURE,
        },
        "season": {
            "n": len(seasons),
            "scored": len(scored),
            "unfilled": sum(1 for r in seasons if r["outcome"] == "unfilled"),
            "med_ret": _med([r["ret"] for r in scored]),
            "med_pred_ret": _med([r["pred_ret"] for r in scored]),
        },
    }


# --- the page ------------------------------------------------------------


def money(c):
    """Copper as the report's colored g/s/c spans."""
    neg = "-" if c < 0 else ""
    c = int(round(abs(c)))
    g, s, k = c // 10000, c % 10000 // 100, c % 100
    out = neg
    if g:
        out += f'<span class="g">{g:,}g</span>&thinsp;'
    if g or s:
        out += f'<span class="s">{s}s</span>&thinsp;'
    return out + f'<span class="c">{k}c</span>'


def pct(v, digits=0):
    return "" if v is None else f"{100 * v:.{digits}f}%"


def _item_link(r):
    return (
        f'<a class="r-{escape(r.get("rarity") or "")}" '
        f'href="https://www.gw2bltc.com/en/item/{r["id"]}">{escape(r["name"])}</a>'
    )


def _rows(rows):
    return "".join(f"<tr>{''.join(f'<td>{c}</td>' for c in r)}</tr>" for r in rows)


def _table(cols, rows):
    if not rows:
        return ""
    head = "".join(f"<th>{c}</th>" for c in cols)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{_rows(rows)}</tbody></table>"


def scoreboard_html(s):
    f = s["flip"]
    if not f["n"]:
        return (
            "<p>No flip pick has resolved yet. Picks resolve once the "
            f"simulated round trip completes or after {FLIP_HORIZON_DAYS} days, "
            "whichever comes first.</p>"
        )
    rt = ""
    if f["med_rt_days"] is not None:
        rt = f"{f['med_rt_days']:.0f}d vs {f['med_pred_rt_days']:.1f}d predicted"
    cap = ""
    if f["med_capture_needed"] is not None:
        cap = (
            f"{pct(f['med_capture_needed'])} vs {pct(f['capture_assumed'])} assumed "
            f"({f['capture_n']} of {f['n']} picks measurable)"
        )
    rows = [
        ("Resolved picks", f"{f['n']:,}"),
        (
            "Completed the round trip",
            f"{f['completed']:,} ({pct(f['completed_pct'])})",
        ),
        ("Filled in part only", f"{f['partial']:,}"),
        ("Never filled a unit", f"{f['unfilled']:,}"),
        (
            "Never reached the front of the book",
            f"{f['never_front']:,} ({pct(f['never_front'] / f['n'])})",
        ),
        ("Net booked", f"{money(f['net'])} vs {money(f['pred_net'])} predicted"),
        ("Realized share of predicted net", pct(f["net_pct"], 1)),
        ("Median round trip, completed picks", rt),
        ("Median capture needed", cap),
    ]
    body = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows if v != "")
    return f'<table class="score"><tbody>{body}</tbody></table>'


def flip_rows(records):
    out = []
    for r in reversed(records):
        if r["kind"] != "flip":
            continue
        out.append(
            (
                _item_link(r),
                r["opened"],
                f'<span class="o-{r["outcome"]}">{r["outcome"]}</span>',
                f"{r['filled']:,.0f} / {r['qty']:,}",
                f"{r['sold']:,.0f}",
                f"{r['buy_front_days']} / {r['days']}",
                f"{r['rt_days']}d" if r["rt_days"] else "",
                f"{r['pred_rt_days']:.1f}d",
                money(r["net"]),
                money(r["pred_net"]),
                pct(r["net_pct"]),
                pct(r["capture_needed"]),
            )
        )
        if len(out) >= TABLE_ROWS:
            break
    return out


def season_open_rows(positions):
    out = []
    for p in positions:
        if p["kind"] != "season":
            continue
        cost = p["spent"] / p["filled"] if p["filled"] > EPS else None
        mark = None
        if cost and p["last_ask"]:
            mark = (p["last_ask"] * (1 - TAX) - cost) / cost
        out.append(
            (
                _item_link(p),
                escape(p["event"]),
                p["opened"],
                f"{p['filled']:,.0f} / {p['qty']:,}",
                money(cost) if cost else "",
                money(p["last_ask"]) if p["last_ask"] else "",
                money(p["limit_price"]),
                pct(mark, 1),
                f"{p['sell_opens']} to {p['sell_closes']}",
                pct(p["pred_ret"]),
            )
        )
    return out[:TABLE_ROWS]


def season_rows(records):
    out = []
    for r in reversed(records):
        if r["kind"] != "season":
            continue
        out.append(
            (
                _item_link(r),
                escape(r["event"]),
                r["opened"],
                r["closed"],
                f'<span class="o-{r["outcome"]}">{r["outcome"]}</span>',
                f"{r['filled']:,.0f} / {r['qty']:,}",
                money(r["avg_cost"]) if r["avg_cost"] else "",
                money(r["exit_price"]) if r["exit_price"] else "",
                pct(r["ret"], 1),
                pct(r["pred_ret"], 1),
            )
        )
        if len(out) >= TABLE_ROWS:
            break
    return out


def render(state, summary, generated):
    o = summary["open"]
    html = TEMPLATE
    html = html.replace("__GENERATED__", generated)
    html = html.replace("__SINCE__", summary["since"] or generated[:10])
    html = html.replace(
        "__OPEN__", f"{o['flip']} flip, {o['season']} seasonal positions open"
    )
    html = html.replace("__SCORE__", scoreboard_html(summary))
    html = html.replace(
        "__FLIPS__",
        _table(
            [
                "Item", "Opened", "Outcome", "Bought", "Sold", "Front d",
                "Round trip", "Predicted trip", "Net", "Predicted net",
                "Realized", "Capture needed",
            ],
            flip_rows(state["records"]),
        )
        or "<p>Nothing resolved yet.</p>",
    )
    s = summary["season"]
    if s["scored"]:
        shead = (
            f"{s['scored']} scored cycle(s), median return {pct(s['med_ret'], 1)} "
            f"against {pct(s['med_pred_ret'], 1)} predicted"
        )
    else:
        shead = (
            "No seasonal cycle has been scored yet; a hold resolves when its "
            "sell window closes, months after the buy."
        )
    html = html.replace("__SEASON_META__", shead)
    open_holds = _table(
        [
            "Item", "Event", "Opened", "Bought", "Avg cost", "Now",
            "Pay up to", "Unrealized", "Sell window", "Predicted ret",
        ],
        season_open_rows(state["positions"]),
    )
    html = html.replace(
        "__SEASON_OPEN__", open_holds or "<p>No hold is open right now.</p>"
    )
    closed_holds = _table(
        [
            "Item", "Event", "Opened", "Closed", "Outcome", "Bought",
            "Avg cost", "Exit", "Realized", "Predicted",
        ],
        season_rows(state["records"]),
    )
    html = html.replace(
        "__SEASON_CLOSED__",
        f"<h3>Resolved holds</h3>{closed_holds}" if closed_holds else "",
    )
    return html


# --- entry point ---------------------------------------------------------


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def main(store=None, snapshot=None, today=None):
    data = _read(SITE / "data.json", None)
    if not data:
        print("no site/data.json; nothing to track")
        return
    picks = data.get("picks") or []
    queue = (_read(SITE / "actions.json", {}) or {}).get("queue") or []

    if store is None:
        from collector.s3store import Store

        store = Store(os.environ["S3_BUCKET"])
    if snapshot is None:
        from collector import dw2

        snapshot = dw2.fetch_snapshot()
    by_id = {it["id"]: it for it in snapshot if it.get("id") is not None}
    today = today or datetime.now(timezone.utc).date()

    state = {
        "since": None,
        "positions": [],
        "records": [],
    }
    prev_open = store.get_json_gz(OPEN_KEY)
    if prev_open:
        state["since"] = prev_open.get("since")
        state["positions"] = prev_open.get("positions") or []
    prev_closed = store.get_json_gz(CLOSED_KEY)
    if prev_closed:
        state["records"] = prev_closed.get("records") or []

    state, stats = roll(state, picks, queue, by_id, today)
    summary = summarize(state)

    store.put_json_gz(
        OPEN_KEY, {"since": state["since"], "positions": state["positions"]}
    )
    store.put_json_gz(CLOSED_KEY, {"records": state["records"]})

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    SITE.mkdir(exist_ok=True)
    (SITE / "track.json").write_text(
        json.dumps(
            {
                "generated": generated,
                "summary": summary,
                "records": state["records"],
                "open": state["positions"],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    (SITE / "track.html").write_text(render(state, summary, generated), encoding="utf-8")

    if stats["dropped"]:
        print(f"dropped {stats['dropped']} oldest positions at the {OPEN_CAP} cap")
    print(
        f"opened {stats['opened']}, resolved {stats['closed']}, "
        f"{len(state['positions'])} open, {len(state['records'])} on record "
        f"-> {SITE / 'track.html'}"
    )


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gw2-advisor: paper track record</title>
<style>
:root { color-scheme: dark; }
body { background: #16181d; color: #d6d8de; font: 14px/1.5 system-ui, sans-serif;
       margin: 0 auto; max-width: 1080px; padding: 24px 16px 48px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 36px 0 4px; }
h3 { font-size: 14px; margin: 24px 0 4px; color: #aab0bd; }
.meta { color: #8a8f9c; font-size: 13px; margin-bottom: 16px; }
.meta a { color: #8ab4f8; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th, td { padding: 5px 8px; text-align: right; white-space: nowrap; }
th { color: #aab0bd; border-bottom: 1px solid #3a3f4b; }
td:first-child, th:first-child { text-align: left; }
tbody tr:nth-child(odd) { background: #1b1e25; }
table.score { width: auto; }
table.score th { text-align: left; border: 0; font-weight: normal; padding-right: 24px; }
table.score td { text-align: left; color: #fff; }
td a { color: inherit; text-decoration: none; }
td a:hover { text-decoration: underline; }
.g { color: #e5c07b; } .s { color: #b4b9c4; } .c { color: #b87352; }
.o-closed { color: #7ec97f; } .o-partial { color: #e0c068; }
.o-unfilled { color: #d97a7a; } .o-marked { color: #8ab4f8; }
.r-Fine { color: #62a4da; } .r-Masterwork { color: #59b135; } .r-Rare { color: #fcd00b; }
.r-Exotic { color: #ffa405; } .r-Ascended { color: #fb3e8d; } .r-Legendary { color: #a675f0; }
.notes { color: #8a8f9c; font-size: 13px; margin-top: 24px; }
.notes ul { margin: 6px 0; padding-left: 20px; }
</style>
</head>
<body>
<h1>gw2-advisor: paper track record</h1>
<div class="meta">
Generated __GENERATED__ &middot; tracking since __SINCE__ &middot; __OPEN__
&middot; <a href="track.json">json</a> &middot; <a href="index.html">report</a>
&middot; <a href="https://github.com/PetrCala/gw2-advisor">source</a>
</div>
<p>Every day's picks are opened as paper positions and replayed against the
following days' market data. No gold is at risk and no order is ever placed;
these are the report's own promises, marked against what the market did.</p>
<h2>Flips</h2>
__SCORE__
<h3>Resolved picks</h3>
__FLIPS__
<div class="notes">
<p>How a position is simulated:</p>
<ul>
<li>The buy order fills only on days the market's best bid has come down to
our price, which is when our order would sit at the front of its queue. On
those days it takes the assumed capture share of the units that actually
filled against buy orders. The sell leg mirrors it against the best ask.</li>
<li>Front d counts the days our buy order was at the front, out of the days
observed. Zero means the market never came to our price: the pick was
priced off the market and could not have filled at all.</li>
<li>Capture needed is the share of the flow that passed our order which we
would have had to absorb to buy the whole lot inside the predicted round
trip. Above the assumed share, the timing was optimistic; blank means the
order was not at the front on any day inside that predicted trip, so no
flow passed it to measure.</li>
<li>Units bought but unsold at resolution are marked at the last observed
bid, net of the 15% cut, so a position that filled and then stalled is
booked at what dumping it would have returned.</li>
</ul>
<p>Fills are simulated, so this cannot measure our own share of the flow
against real competitors: only real gold can. What it does measure is
whether the placement was reachable and whether the flow behind the timing
was there. Observations are daily, so round trips shorter than a day all
read as one day.</p>
</div>
<h2>Seasonal holds</h2>
<div class="meta">__SEASON_META__</div>
<h3>Open holds</h3>
__SEASON_OPEN__
__SEASON_CLOSED__
<div class="notes">
<p>A seasonal pick is opened the day it enters the buy-now queue, buys off
listings on the days the ask sits under its pay-up-to ceiling, and is marked
out over the prices observed inside its sell window. Cycles run for months,
so most holds sit open; one that ages out before its window is marked at the
last ask and labelled marked, not scored.</p>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    main()
