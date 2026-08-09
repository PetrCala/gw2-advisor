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
- A craft-carry pick (docs/INVESTING.md, S8) crafts once a day at that day's
  own cost and revenue for as long as the refinement keeps pricing, and
  resolves after CRAFT_HORIZON_DAYS: no queue to reach, since the inputs are
  bulk staples bought and the output sold at one order-width inside the
  book, the same convention invest.craft prices them with.
- A luxury desk pick (docs/INVESTING.md, S4) fills its patient bid and
  relist ask the same way a flip's legs do, at the same capture assumption,
  just against a much longer horizon (LUXURY_HORIZON_DAYS) matching this
  tier's weeks-long round trips. The daily snapshot row carries no order
  book, so a position that goes stale past invest.luxury.PATIENCE_DAYS is
  flagged (went_stale) rather than actually repriced mid-simulation; the
  live reprice happens in invest.luxury.build_picks at collection time.
- A corner pick (docs/INVESTING.md, S6) fills its whole lot at open at the
  ladder VWAP the screen computed, then the exit is simulated day by day:
  we sell our planned share of demand on days nobody has undercut our
  relist, and abort once new listings have outrun our own sales by the
  schedule's tripwire. The entry is not simulated because a sweep is one instant
  action against a book that was really observed, and the daily snapshot
  carries no ladder to model it against.

Every opened position carries a "strategy" tag (docs/INVESTING.md,
benchmarks: "every recommendation the long book emits ... with the strategy
tag carried through"), so a future scoreboard can group hit rate and
realized return by strategy rather than just by kind.

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

It reads the freshly built site/data.json and site/actions.json, the
same-run invest/craft.json.gz point and invest/luxury/latest.json.gz
artifact, and costs one extra datawars2 snapshot request a day, the same
one the build makes.
"""

import json
import math
import os
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from statistics import median

from invest import bounds, corner, luxury, risk
from invest.corner import CORNER_KEY
from invest.craft import CRAFT_KEY
from invest.luxury import LUXURY_KEY
from invest.thesis import THESIS_HORIZON_DAYS
from scorers import flip

SITE = Path(__file__).resolve().parent.parent / "site"

OPEN_KEY = "track/open.json.gz"
CLOSED_KEY = "track/closed.json.gz"

TAX = flip.TAX
CAPTURE = flip.CAPTURE  # the assumption under test, read from the scorer itself
# Same number, different meaning: in a corner we are the only seller by
# construction, so this is a demand haircut for the raised price rather
# than a share of contested flow. Read from the screen that sized the lot.
CORNER_UNLOAD_SHARE = corner.UNLOAD_SHARE

FLIP_HORIZON_DAYS = 7  # a 2-day predicted round trip gets a week to happen
SEASON_HORIZON_DAYS = 400  # one full cycle plus slack, then mark it out
CRAFT_HORIZON_DAYS = 7  # matches S8's "sell weekly" cadence
LUXURY_HORIZON_DAYS = 70  # entry patience (21d) + exit sizing horizon (30d) + slack
CORNER_HORIZON_DAYS = 120  # the 90-day unload horizon plus slack, same construction as luxury's
FLIP_PER_DAY = flip.TOP_N  # the picks as published, no wider
LUXURY_PER_DAY = 20  # S4 sizing: 10-20 items concurrently
OPEN_CAP = 800  # backstop on state size; the horizons bound it well below this
CLOSED_CAP = 400
TABLE_ROWS = 40  # rows rendered per table; the json carries everything
EPS = 1e-9

STRATEGY = {
    "flip": "flip",
    "season": "festival carry",
    "craft": "time-gated crafting",
    "luxury": "luxury desk",
    "thesis": "thesis registry",
    "corner": "corners",
}

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
        "strategy": STRATEGY["flip"],
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
        "strategy": STRATEGY["season"],
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


def open_craft(chain, today):
    """A live craft-carry reading as a paper position; id is the chain's
    key (a string), not a GW2 item id, since one refinement has no single
    item to key on. output_id carries the tradable item for display links.
    """
    return {
        "v": SCHEMA,
        "kind": "craft",
        "strategy": STRATEGY["craft"],
        "id": chain["key"],
        "name": chain["name"],
        "output_id": chain["output_id"],
        "opened": today.isoformat(),
        "pred_spread": chain["spread"],
        "days": 0,
        "crafted": 0,
        "spent": 0.0,
        "revenue": 0.0,
        "last_spread": chain["spread"],
        "last_tick": None,
    }


def open_luxury(pick, today):
    """A luxury desk pick (invest.luxury.build_picks) as a paper position."""
    return {
        "v": SCHEMA,
        "kind": "luxury",
        "strategy": STRATEGY["luxury"],
        "id": pick["id"],
        "name": pick.get("name") or f"item {pick['id']}",
        "rarity": pick.get("rarity") or "",
        "opened": today.isoformat(),
        "buy_at": pick["buy_at"],
        "sell_at": pick["sell_at"],
        "qty": pick["qty"],
        "margin": pick["margin"],
        "days": 0,
        "filled": 0.0,
        "sold": 0.0,
        "spent": 0.0,
        "revenue": 0.0,
        "buy_front_days": 0,
        "sell_front_days": 0,
        "filled_day": None,
        "sold_day": None,
        "went_stale": False,
        "last_bid": None,
        "last_ask": None,
        "last_tick": None,
    }


def open_thesis(row, today):
    """A thesis registry entry (bucket == "active") as a paper position,
    near-verbatim copy of open_flip. id is the registry's own string key
    (invest.craft.CHAINS's own "key" convention), not the GW2 item id;
    item_id carries that separately for ticking against the daily
    snapshot and for display links. buy_at/sell_at are entry_ceiling/
    target taken as-is (already set by the human and sized by
    invest.thesis, not gap-walked here)."""
    return {
        "v": SCHEMA,
        "kind": "thesis",
        "strategy": STRATEGY["thesis"],
        "id": row["key"],
        "item_id": row["id"],
        "name": row.get("name") or f"item {row['id']}",
        "opened": today.isoformat(),
        "buy_at": row["entry_ceiling"],
        "sell_at": row["target"],
        "qty": row["qty"],
        "margin": row["target"] - row["entry_ceiling"],
        "invalidation_date": row.get("invalidation_date"),
        "days": 0,
        "filled": 0.0,
        "sold": 0.0,
        "spent": 0.0,
        "revenue": 0.0,
        "buy_front_days": 0,
        "sell_front_days": 0,
        "filled_day": None,
        "sold_day": None,
        "closed_by_human": False,
        "last_bid": None,
        "last_ask": None,
        "last_tick": None,
    }


def open_corner(pick, today):
    """A corner screen pick (invest.corner.build_picks) as a paper position.

    The whole lot is filled at open, at the ladder VWAP the screen computed
    off a book that was really observed. A sweep is one instant action
    against that ladder, and the daily snapshot row carries no ladder to
    simulate it against, so inventing a fill mechanic out of an aggregate
    sell_quantity would add fake precision to the one leg that is not in
    doubt. The exit is where the fidelity belongs, and that is what
    tick_corner models.
    """
    unload = pick.get("unload") or {}
    return {
        "v": SCHEMA,
        "kind": "corner",
        "strategy": STRATEGY["corner"],
        "id": pick["id"],
        "name": pick.get("name") or f"item {pick['id']}",
        "rarity": pick.get("rarity") or "",
        "opened": today.isoformat(),
        "qty": pick["qty"],
        "avg_cost": pick["avg_cost"],
        "relist_at": pick["relist_at"],
        "ceiling": pick["ceiling"],
        "ceiling_source": pick.get("ceiling_source") or "",
        "margin": pick["margin"],
        "unload_per_day": unload.get("per_day") or 0.0,
        "planned_days": unload.get("days"),
        "abort_qty": unload.get("abort_qty") or 0,
        "entry_modeled": "ladder VWAP at open",
        "days": 0,
        "spent": float(pick["qty"] * pick["avg_cost"]),
        "sold": 0.0,
        "revenue": 0.0,
        "held_top_days": 0,
        "undercut_days": 0,
        "resupply": 0.0,
        "aborted": False,
        "last_bid": None,
        "last_ask": None,
        "last_tick": None,
    }


# --- daily observation ---------------------------------------------------


def tick_corner(pos, row, today):
    """Advance one corner by a day: did we still own the top of the book?

    On a day the best ask is at or above our relist, nobody has undercut us,
    so we sell our own planned share of that day's demand at the relist net
    of tax. On a day it is below, we sell nothing and the day is counted as
    undercut.

    The abort tripwire compares re-supply since open against OUR OWN SALES
    since open, and fires when new listings have outrun us by one week of
    the planned schedule. Comparing the cumulative re-supply flow against
    that same one-week stock instead would make the tripwire a clock: any
    refill rate reaches any fixed quantity eventually, so a position would
    abort on day 28 of a 90-day plan purely from elapsed time, and the
    "resupplied" bucket that is meant to be S6's scoreboard would be partly
    measuring the calendar. Net, it fires when the moat is actually
    breaking, which is the 2016 ToT-bag festival re-supply and the 2016-17
    leather-farm patch.
    """
    bid, ask = row.get("buy_price"), row.get("sell_price")
    pos["days"] += 1
    if bid:
        pos["last_bid"] = bid
    if ask:
        pos["last_ask"] = ask
    pos["resupply"] += _f(row.get("1d_sell_listed"))

    left = pos["qty"] - pos["sold"]
    if left <= EPS:
        return
    if ask and ask >= pos["relist_at"]:
        pos["held_top_days"] += 1
        units = min(left, CORNER_UNLOAD_SHARE * _f(row.get("1d_sell_sold")))
        pos["sold"] += units
        pos["revenue"] += units * pos["relist_at"] * (1 - TAX)
    elif ask:
        pos["undercut_days"] += 1
    if (
        pos["abort_qty"]
        and net_resupply(pos) >= pos["abort_qty"]
        and pos["qty"] - pos["sold"] > EPS
    ):
        pos["aborted"] = True


def net_resupply(pos):
    """New listings since open minus our own sales since open, floored at
    zero: how far the book has refilled past what we cleared out of it."""
    return max(0.0, pos["resupply"] - pos["sold"])


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


def tick_craft(pos, chain_today, today):
    """Advance one craft-carry position by a day of fresh chain pricing.

    Crafts once at that day's own cost and revenue when the chain still
    prices a spread; a day it stops pricing (a stale market, or the fifth
    refinement's missing output) is simply not crafted, so a position can
    resolve having crafted fewer than `days` times.
    """
    pos["days"] += 1
    cost = chain_today.get("cost") if chain_today else None
    revenue = chain_today.get("revenue") if chain_today else None
    if cost is not None and revenue is not None:
        pos["crafted"] += 1
        pos["spent"] += cost
        pos["revenue"] += revenue
        pos["last_spread"] = chain_today["spread"]


def tick_luxury(pos, row, today):
    """Advance one luxury position by a day: the same fill mechanic as
    tick_flip, at this tier's own patience horizon. The snapshot row
    carries no order book to reprice a stale bid against, so an unfilled
    order past invest.luxury.PATIENCE_DAYS is flagged (went_stale) rather
    than actually repriced; invest.luxury.build_picks does that live, at
    collection time, against the real book.
    """
    bid, ask = row.get("buy_price"), row.get("sell_price")
    pos["days"] += 1
    if bid:
        pos["last_bid"] = bid
    if ask:
        pos["last_ask"] = ask

    if pos["filled"] < pos["qty"] - EPS and bid and bid <= pos["buy_at"]:
        flow = _f(row.get("1d_buy_sold"))
        pos["buy_front_days"] += 1
        units = min(pos["qty"] - pos["filled"], CAPTURE * flow)
        pos["filled"] += units
        pos["spent"] += units * pos["buy_at"]
        if pos["filled"] >= pos["qty"] - EPS and pos["filled_day"] is None:
            pos["filled_day"] = pos["days"]
    elif not pos["went_stale"]:
        bucket, _ = luxury.patience_verdict(pos["days"], pos["filled"])
        pos["went_stale"] = bucket == "reprice"

    if pos["sold"] < pos["filled"] - EPS and ask and ask >= pos["sell_at"]:
        flow = _f(row.get("1d_sell_sold"))
        pos["sell_front_days"] += 1
        units = min(pos["filled"] - pos["sold"], CAPTURE * flow)
        pos["sold"] += units
        pos["revenue"] += units * (pos["sell_at"] - 1) * (1 - TAX)
        if pos["sold"] >= pos["qty"] - EPS and pos["sold_day"] is None:
            pos["sold_day"] = pos["days"]


def tick_thesis(pos, row, today):
    """Advance one thesis position by a day: copy of tick_flip, ticked
    against the daily snapshot row for invest.thesis's own item id."""
    bid, ask = row.get("buy_price"), row.get("sell_price")
    pos["days"] += 1
    if bid:
        pos["last_bid"] = bid
    if ask:
        pos["last_ask"] = ask

    if pos["filled"] < pos["qty"] - EPS and bid and bid <= pos["buy_at"]:
        flow = _f(row.get("1d_buy_sold"))
        pos["buy_front_days"] += 1
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
        pos["revenue"] += units * (pos["sell_at"] - 1) * (1 - TAX)
        if pos["sold"] >= pos["qty"] - EPS and pos["sold_day"] is None:
            pos["sold_day"] = pos["days"]


TICKS = {
    "flip": tick_flip, "season": tick_season, "craft": tick_craft,
    "luxury": tick_luxury, "thesis": tick_thesis, "corner": tick_corner,
}


# --- resolution ----------------------------------------------------------


def is_done(pos, today):
    """A flip or luxury pick resolves on a finished round trip or at its
    horizon; a seasonal hold when its sell window has passed, or early once
    its entry is gone; a craft carry weekly, on a fixed horizon
    (docs/INVESTING.md's "sell weekly" cadence); a thesis position on a
    target hit, the registry's own invalidation date, a human status:
    closed override (checked first: it is how the human records that the
    real in-game decision is already made), or the safety-net horizon,
    whichever comes first; a corner when the lot is gone, when re-supply
    tripped the abort, or at its own longer horizon."""
    iso = today.isoformat()
    age = _days(pos["opened"], today)
    if pos["kind"] == "flip":
        return pos["sold"] >= pos["qty"] - EPS or age >= FLIP_HORIZON_DAYS
    if pos["kind"] == "craft":
        return age >= CRAFT_HORIZON_DAYS
    if pos["kind"] == "luxury":
        return pos["sold"] >= pos["qty"] - EPS or age >= LUXURY_HORIZON_DAYS
    if pos["kind"] == "corner":
        if pos.get("aborted"):
            return True
        return pos["sold"] >= pos["qty"] - EPS or age >= CORNER_HORIZON_DAYS
    if pos["kind"] == "thesis":
        if pos.get("closed_by_human"):
            return True
        if pos["sold"] >= pos["qty"] - EPS:
            return True
        if pos.get("invalidation_date") and iso > pos["invalidation_date"]:
            return True
        return age >= THESIS_HORIZON_DAYS
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
        "strategy": pos.get("strategy", STRATEGY["flip"]),
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
        "strategy": pos.get("strategy", STRATEGY["season"]),
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


def close_craft(pos, today):
    """Book the week: net crafted revenue against what was spent crafting it.

    outcome is "closed" once at least one day actually crafted (the chain
    priced a spread that day), "unfilled" when the refinement never priced
    all week, matching the flip/season vocabulary of a position that never
    got a chance to prove anything.
    """
    net = pos["revenue"] - pos["spent"]
    outcome = "closed" if pos["crafted"] else "unfilled"
    return {
        "kind": "craft",
        "strategy": pos.get("strategy", STRATEGY["craft"]),
        "id": pos["id"],
        "name": pos["name"],
        "output_id": pos["output_id"],
        "opened": pos["opened"],
        "closed": today.isoformat(),
        "outcome": outcome,
        "days": pos["days"],
        "crafted": pos["crafted"],
        "spent": round(pos["spent"], 1),
        "revenue": round(pos["revenue"], 1),
        "net": round(net, 1),
        "net_pct": round(net / pos["spent"], 3) if pos["spent"] > 0 else None,
        "pred_spread": pos["pred_spread"],
    }


def close_luxury(pos, today):
    """Book the position: same accounting as close_flip, this tier's own
    horizon and a went_stale flag instead of a pred_rt_days comparison
    (there is no single predicted trip length once patience repricing is
    part of the plan)."""
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
        "kind": "luxury",
        "strategy": pos.get("strategy", STRATEGY["luxury"]),
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
        "went_stale": pos["went_stale"],
        "pred_net": round(pred_net, 1),
        "net": round(net, 1),
        "net_pct": round(net / pred_net, 3) if pred_net > 0 else None,
    }


def close_thesis(pos, today):
    """Book the position: copy of close_flip's accounting, with an outcome
    vocabulary distinct from flip's (target_hit/invalidated/
    horizon_expired/closed_by_human) since a thesis resolves against the
    registry's own dates and overrides rather than a predicted trip
    length."""
    held = max(0.0, pos["filled"] - pos["sold"])
    mark = pos["last_bid"] or pos["buy_at"]
    residual = held * mark * (1 - TAX)
    net = pos["revenue"] + residual - pos["spent"]
    pred_net = pos["margin"] * pos["qty"]
    iso = today.isoformat()
    if pos.get("closed_by_human"):
        outcome = "closed_by_human"
    elif pos["sold"] >= pos["qty"] - EPS:
        outcome = "target_hit"
    elif pos.get("invalidation_date") and iso > pos["invalidation_date"]:
        outcome = "invalidated"
    else:
        outcome = "horizon_expired"
    return {
        "kind": "thesis",
        "strategy": pos.get("strategy", STRATEGY["thesis"]),
        "id": pos["id"],
        "item_id": pos["item_id"],
        "name": pos["name"],
        "opened": pos["opened"],
        "closed": today.isoformat(),
        "outcome": outcome,
        "qty": pos["qty"],
        "filled": round(pos["filled"], 1),
        "sold": round(pos["sold"], 1),
        "days": pos["days"],
        "buy_front_days": pos["buy_front_days"],
        "sell_front_days": pos["sell_front_days"],
        "pred_net": round(pred_net, 1),
        "net": round(net, 1),
        "net_pct": round(net / pred_net, 3) if pred_net > 0 else None,
    }


def close_corner(pos, today):
    """Book the corner: the exit is the scoreboard, so the outcome
    vocabulary names how the exit went (unloaded / partial / undercut /
    resupplied / horizon_expired) rather than whether a round trip
    completed. Unsold units are marked at the last bid net of fees, exactly
    like close_flip."""
    held = max(0.0, pos["qty"] - pos["sold"])
    mark = pos["last_bid"] or pos["avg_cost"]
    residual = held * mark * (1 - TAX)
    net = pos["revenue"] + residual - pos["spent"]
    pred_net = pos["margin"] * pos["qty"]
    if pos["sold"] >= pos["qty"] - EPS:
        outcome = "unloaded"
    elif pos.get("aborted"):
        outcome = "resupplied"
    elif not pos["held_top_days"]:
        outcome = "undercut"
    elif pos["sold"] > EPS:
        outcome = "partial"
    else:
        outcome = "horizon_expired"
    return {
        "kind": "corner",
        "strategy": pos.get("strategy", STRATEGY["corner"]),
        "id": pos["id"],
        "name": pos["name"],
        "rarity": pos["rarity"],
        "opened": pos["opened"],
        "closed": today.isoformat(),
        "outcome": outcome,
        "qty": pos["qty"],
        "sold": round(pos["sold"], 1),
        "days": pos["days"],
        "held_top_days": pos["held_top_days"],
        "undercut_days": pos["undercut_days"],
        "planned_days": pos["planned_days"],
        "resupply": round(pos["resupply"], 1),
        "net_resupply": round(net_resupply(pos), 1),
        "abort_qty": pos["abort_qty"],
        "ceiling_source": pos["ceiling_source"],
        "spent": round(pos["spent"], 1),
        "revenue": round(pos["revenue"], 1),
        "pred_net": round(pred_net, 1),
        "net": round(net, 1),
        "net_pct": round(net / pred_net, 3) if pred_net > 0 else None,
    }


CLOSERS = {
    "flip": close_flip, "season": close_season, "craft": close_craft,
    "luxury": close_luxury, "thesis": close_thesis, "corner": close_corner,
}


# --- the daily roll ------------------------------------------------------


def roll(state, picks, queue, by_id, today, crafts=(), luxury_picks=(), theses=(),
         corner_picks=()):
    """One day of tracking: observe, resolve, then open today's picks.

    Positions are keyed by (kind, item): an item already being tracked is
    not opened again until its position resolves, so the record is a set of
    independent samples rather than the same pick counted seven times.
    crafts is invest.craft's latest per-chain readings (spread_of's dicts);
    craft positions key on the chain's string key rather than a GW2 item id,
    so they get their own lookup instead of sharing by_id. luxury_picks is
    invest.luxury.build_picks' output; those key on the GW2 item id, so they
    share by_id like flip picks do. theses is invest.thesis.enrich_all's
    output (every bucket, not just active): thesis positions tick against
    by_id via their own item id, and a status: closed override in the
    live registry marks the matching open position for immediate closure.
    corner_picks is invest.corner.build_picks' actionable output; at most
    invest.corner.MAX_CONCURRENT_CORNERS are open at any time, because a
    corner ties its whole lot up for months rather than recycling capital
    the way a flip does.
    """
    iso = today.isoformat()
    positions = [p for p in (state.get("positions") or []) if p.get("v") == SCHEMA]
    records = list(state.get("records") or [])
    since = state.get("since") or iso
    by_craft = {c["key"]: c for c in crafts}
    by_thesis = {t["key"]: t for t in theses if t.get("key")}

    live, closed_now = [], []
    for pos in positions:
        if pos["kind"] == "craft":
            row = by_craft.get(pos["id"])
        elif pos["kind"] == "thesis":
            row = by_id.get(pos.get("item_id"))
            treg = by_thesis.get(pos["id"])
            if treg and treg.get("status") == "closed":
                pos["closed_by_human"] = True
        else:
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
    for c in crafts:
        if c.get("dead") is not False or ("craft", c["key"]) in keys:
            continue
        live.append(open_craft(c, today))
        keys.add(("craft", c["key"]))
        opened += 1
    for p in luxury_picks[:LUXURY_PER_DAY]:
        if ("luxury", p["id"]) in keys:
            continue
        live.append(open_luxury(p, today))
        keys.add(("luxury", p["id"]))
        opened += 1
    for t in theses:
        if t.get("bucket") != "active" or not t.get("qty"):
            continue
        if ("thesis", t.get("key")) in keys:
            continue
        live.append(open_thesis(t, today))
        keys.add(("thesis", t["key"]))
        opened += 1
    room = corner.MAX_CONCURRENT_CORNERS - sum(1 for p in live if p["kind"] == "corner")
    for p in corner_picks:
        if room <= 0:
            break
        if p.get("score", 0) < corner.MIN_SCORE or ("corner", p["id"]) in keys:
            continue
        live.append(open_corner(p, today))
        keys.add(("corner", p["id"]))
        opened += 1
        room -= 1

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
    crafts = [r for r in records if r["kind"] == "craft"]
    luxuries = [r for r in records if r["kind"] == "luxury"]
    theses = [r for r in records if r["kind"] == "thesis"]
    corners = [r for r in records if r["kind"] == "corner"]
    n = len(flips)
    completed = [r for r in flips if r["outcome"] == "closed"]
    scored = [r for r in seasons if r["outcome"] == "closed" and r["ret"] is not None]
    craft_closed = [r for r in crafts if r["outcome"] == "closed"]
    lux_completed = [r for r in luxuries if r["outcome"] == "closed"]
    thesis_completed = [r for r in theses if r["outcome"] in ("target_hit", "closed_by_human")]
    pred_net = sum(r["pred_net"] for r in flips)
    craft_spent = sum(r["spent"] for r in crafts)
    lux_pred_net = sum(r["pred_net"] for r in luxuries)
    thesis_pred_net = sum(r["pred_net"] for r in theses)
    corner_spent = sum(r["spent"] for r in corners)
    # The design doc says the exit is the binding constraint, so the corner
    # scoreboard leads with the exit: days spent owning the top of the book
    # against the days the unload schedule planned for, and the share of
    # positions that ended with the moat gone. Net gold is secondary.
    exit_broken = sum(1 for r in corners if r["outcome"] in ("undercut", "resupplied"))
    return {
        "since": state.get("since"),
        "open": {
            "flip": sum(1 for p in positions if p["kind"] == "flip"),
            "season": sum(1 for p in positions if p["kind"] == "season"),
            "craft": sum(1 for p in positions if p["kind"] == "craft"),
            "luxury": sum(1 for p in positions if p["kind"] == "luxury"),
            "thesis": sum(1 for p in positions if p["kind"] == "thesis"),
            "corner": sum(1 for p in positions if p["kind"] == "corner"),
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
        "craft": {
            "n": len(crafts),
            "completed": len(craft_closed),
            "unfilled": sum(1 for r in crafts if r["outcome"] == "unfilled"),
            "net": round(sum(r["net"] for r in crafts)),
            "net_pct": round(sum(r["net"] for r in crafts) / craft_spent, 3)
            if craft_spent > 0
            else None,
            "med_net_pct": _med([r["net_pct"] for r in crafts]),
        },
        "luxury": {
            "n": len(luxuries),
            "completed": len(lux_completed),
            "partial": sum(1 for r in luxuries if r["outcome"] == "partial"),
            "unfilled": sum(1 for r in luxuries if r["outcome"] == "unfilled"),
            "never_front": sum(1 for r in luxuries if not r["buy_front_days"]),
            "completed_pct": round(len(lux_completed) / len(luxuries), 3)
            if luxuries
            else None,
            "pred_net": round(lux_pred_net),
            "net": round(sum(r["net"] for r in luxuries)),
            "net_pct": round(sum(r["net"] for r in luxuries) / lux_pred_net, 3)
            if lux_pred_net > 0
            else None,
            "med_net_pct": _med([r["net_pct"] for r in luxuries]),
            "went_stale": sum(1 for r in luxuries if r["went_stale"]),
        },
        "thesis": {
            "n": len(theses),
            "completed": len(thesis_completed),
            "net": round(sum(r["net"] for r in theses)),
            "net_pct": round(sum(r["net"] for r in theses) / thesis_pred_net, 3)
            if thesis_pred_net > 0
            else None,
            "med_net_pct": _med([r["net_pct"] for r in theses]),
        },
        "corner": {
            "n": len(corners),
            "unloaded": sum(1 for r in corners if r["outcome"] == "unloaded"),
            "partial": sum(1 for r in corners if r["outcome"] == "partial"),
            "undercut": sum(1 for r in corners if r["outcome"] == "undercut"),
            "resupplied": sum(1 for r in corners if r["outcome"] == "resupplied"),
            "exit_broken_pct": round(exit_broken / len(corners), 3) if corners else None,
            "med_top_days": _med([r["held_top_days"] for r in corners]),
            "med_planned_days": _med([r["planned_days"] for r in corners]),
            "net": round(sum(r["net"] for r in corners)),
            "net_pct": round(sum(r["net"] for r in corners) / corner_spent, 3)
            if corner_spent > 0
            else None,
            "med_net_pct": _med([r["net_pct"] for r in corners]),
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


def _floor_flag(item_id, by_id):
    """"62% vendor" / "at floor vendor" for a position sitting near a
    computable structural floor (docs/INVESTING.md risk rule 7, S7), or ""
    when there is no item id, no snapshot row for it, or no floor at all.

    Recomputed fresh at render time and never stored, exactly like
    _launch_flag below, so SCHEMA stays put and no stored position is
    invalidated by this column arriving.

    Only the vendor leg is wired: it is free (vendor_value already rides in
    the default snapshot fields), while the conversion leg needs a price map
    this module does not fetch. No graph/prices parameters are carried for a
    caller that might one day have both, because an accepted-but-never-passed
    parameter reads as covered when it is not.
    """
    if item_id is None or not by_id:
        return ""
    row = by_id.get(item_id)
    if not row:
        return ""
    out = bounds.structural_floor(item_id, row, {}, None)
    coverage, source = out["coverage"], out["source"]
    if coverage is None:
        return ""
    if coverage >= 1:
        return f"at floor {source}"
    return f"{coverage:.0%} {source}"


def _launch_flag(item_id, today):
    """"Name in Nd" for a farmable position approaching an expansion
    launch, computed fresh at render time (never stored, like every other
    calendar field in this module); "" without one or without an item id
    at all (a craft chain with no single tradable output never flags)."""
    if item_id is None:
        return ""
    found = risk.launch_risk(item_id, today)
    if not found:
        return ""
    label, days_until = found
    return f"{label} in {days_until}d"


def season_open_rows(positions, today, by_id=None):
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
                _launch_flag(p["id"], today),
                _floor_flag(p["id"], by_id),
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


def _craft_link(r):
    if not r.get("output_id"):
        return escape(r["name"])
    return (
        f'<a href="https://www.gw2bltc.com/en/item/{r["output_id"]}">'
        f'{escape(r["name"])}</a>'
    )


def craft_open_rows(positions, today, by_id=None):
    out = []
    for p in positions:
        if p["kind"] != "craft":
            continue
        out.append(
            (
                _craft_link(p),
                p["opened"],
                f"{p['crafted']} / {p['days']}",
                money(p["spent"]),
                money(p["revenue"]),
                pct(p["last_spread"], 1),
                _launch_flag(p.get("output_id"), today),
                _floor_flag(p.get("output_id"), by_id),
            )
        )
    return out[:TABLE_ROWS]


def craft_rows(records):
    out = []
    for r in reversed(records):
        if r["kind"] != "craft":
            continue
        out.append(
            (
                _craft_link(r),
                r["opened"],
                r["closed"],
                f'<span class="o-{r["outcome"]}">{r["outcome"]}</span>',
                f"{r['crafted']} / {r['days']}",
                money(r["spent"]),
                money(r["revenue"]),
                money(r["net"]),
                pct(r["net_pct"], 1),
                pct(r["pred_spread"], 1),
            )
        )
        if len(out) >= TABLE_ROWS:
            break
    return out


def luxury_scoreboard_html(s):
    l = s["luxury"]
    if not l["n"]:
        return (
            "<p>No luxury desk pick has resolved yet. Picks resolve once the "
            f"simulated round trip completes or after {LUXURY_HORIZON_DAYS} days, "
            "whichever comes first.</p>"
        )
    rows = [
        ("Resolved picks", f"{l['n']:,}"),
        (
            "Completed the round trip",
            f"{l['completed']:,} ({pct(l['completed_pct'])})",
        ),
        ("Filled in part only", f"{l['partial']:,}"),
        ("Never filled a unit", f"{l['unfilled']:,}"),
        (
            "Never reached the front of the book",
            f"{l['never_front']:,} ({pct(l['never_front'] / l['n'])})",
        ),
        ("Net booked", f"{money(l['net'])} vs {money(l['pred_net'])} predicted"),
        ("Realized share of predicted net", pct(l["net_pct"], 1)),
        (
            "Went stale past the patience window",
            f"{l['went_stale']:,} ({pct(l['went_stale'] / l['n'])})",
        ),
    ]
    body = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows if v != "")
    return f'<table class="score"><tbody>{body}</tbody></table>'


def luxury_open_rows(positions, today, by_id=None):
    out = []
    for p in positions:
        if p["kind"] != "luxury":
            continue
        out.append(
            (
                _item_link(p),
                p["opened"],
                f"{p['filled']:,.0f} / {p['qty']:,}",
                f"{p['sold']:,.0f}",
                money(p["buy_at"]),
                money(p["sell_at"]),
                money(p["last_bid"]) if p["last_bid"] else "",
                money(p["last_ask"]) if p["last_ask"] else "",
                f"{p['days']}d",
                "stale" if p["went_stale"] else "",
                _launch_flag(p["id"], today),
                _floor_flag(p["id"], by_id),
            )
        )
    return out[:TABLE_ROWS]


def luxury_rows(records):
    out = []
    for r in reversed(records):
        if r["kind"] != "luxury":
            continue
        out.append(
            (
                _item_link(r),
                r["opened"],
                f'<span class="o-{r["outcome"]}">{r["outcome"]}</span>',
                f"{r['filled']:,.0f} / {r['qty']:,}",
                f"{r['sold']:,.0f}",
                f"{r['buy_front_days']} / {r['days']}",
                money(r["net"]),
                money(r["pred_net"]),
                pct(r["net_pct"]),
                "stale" if r["went_stale"] else "",
            )
        )
        if len(out) >= TABLE_ROWS:
            break
    return out


def _thesis_link(p):
    return (
        f'<a href="https://www.gw2bltc.com/en/item/{p["item_id"]}">'
        f'{escape(p["name"])}</a>'
    )


def thesis_open_rows(positions, today, by_id=None):
    out = []
    for p in positions:
        if p["kind"] != "thesis":
            continue
        out.append(
            (
                _thesis_link(p),
                p["opened"],
                f"{p['filled']:,.0f} / {p['qty']:,}",
                f"{p['sold']:,.0f}",
                money(p["buy_at"]),
                money(p["sell_at"]),
                money(p["last_bid"]) if p["last_bid"] else "",
                money(p["last_ask"]) if p["last_ask"] else "",
                f"{p['days']}d",
                _launch_flag(p["item_id"], today),
                _floor_flag(p["item_id"], by_id),
            )
        )
    return out[:TABLE_ROWS]


def thesis_rows(records):
    out = []
    for r in reversed(records):
        if r["kind"] != "thesis":
            continue
        out.append(
            (
                _thesis_link(r),
                r["opened"],
                r["closed"],
                f'<span class="o-{r["outcome"]}">{r["outcome"]}</span>',
                f"{r['filled']:,.0f} / {r['qty']:,}",
                f"{r['sold']:,.0f}",
                money(r["net"]),
                money(r["pred_net"]),
                pct(r["net_pct"]),
            )
        )
        if len(out) >= TABLE_ROWS:
            break
    return out


def corner_open_rows(positions, today, by_id=None):
    out = []
    for p in positions:
        if p["kind"] != "corner":
            continue
        planned = f"{p['planned_days']}d" if p.get("planned_days") else ""
        out.append(
            (
                _item_link(p),
                p["opened"],
                f"{p['sold']:,.0f} / {p['qty']:,}",
                money(p["avg_cost"]),
                money(p["relist_at"]),
                escape(p["ceiling_source"]),
                f"{p['held_top_days']} / {p['days']}",
                f"{p['undercut_days']}",
                f"{net_resupply(p):,.0f} / {p['abort_qty']:,}",
                planned,
                _launch_flag(p["id"], today),
                _floor_flag(p["id"], by_id),
            )
        )
    return out[:TABLE_ROWS]


def corner_rows(records):
    out = []
    for r in reversed(records):
        if r["kind"] != "corner":
            continue
        out.append(
            (
                _item_link(r),
                r["opened"],
                r["closed"],
                f'<span class="o-{r["outcome"]}">{r["outcome"]}</span>',
                f"{r['sold']:,.0f} / {r['qty']:,}",
                f"{r['held_top_days']} / {r['days']}",
                f"{r['net_resupply']:,.0f} / {r['abort_qty']:,}",
                money(r["net"]),
                money(r["pred_net"]),
                pct(r["net_pct"]),
            )
        )
        if len(out) >= TABLE_ROWS:
            break
    return out


def render(state, summary, generated, by_id=None):
    """The page. by_id is the day's dw2 snapshot keyed by item id, used only
    for the recomputed-at-render Floor column; absent, that column renders
    empty rather than the build failing."""
    o = summary["open"]
    today = datetime.now(timezone.utc).date()
    html = TEMPLATE
    html = html.replace("__GENERATED__", generated)
    html = html.replace("__SINCE__", summary["since"] or generated[:10])
    html = html.replace(
        "__OPEN__",
        f"{o['flip']} flip, {o['season']} seasonal, {o['craft']} craft-carry, "
        f"{o['luxury']} luxury desk, {o['thesis']} thesis, "
        f"{o['corner']} corner positions open",
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
            "Launch risk", "Floor",
        ],
        season_open_rows(state["positions"], today, by_id),
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
    c = summary["craft"]
    if c["completed"]:
        chead = (
            f"{c['completed']} resolved week(s), net {money(c['net'])}, median "
            f"realized {pct(c['med_net_pct'], 1)} of spend"
        )
    else:
        chead = (
            "No craft-carry week has resolved yet; a position resolves "
            f"after {CRAFT_HORIZON_DAYS} days."
        )
    html = html.replace("__CRAFT_META__", chead)
    craft_open = _table(
        ["Refinement", "Opened", "Crafted", "Spent", "Revenue", "Spread",
         "Launch risk", "Floor"],
        craft_open_rows(state["positions"], today, by_id),
    )
    html = html.replace(
        "__CRAFT_OPEN__", craft_open or "<p>No craft carry is open right now.</p>"
    )
    craft_closed = _table(
        ["Refinement", "Opened", "Closed", "Outcome", "Crafted", "Spent",
         "Revenue", "Net", "Realized", "Predicted"],
        craft_rows(state["records"]),
    )
    html = html.replace(
        "__CRAFT_CLOSED__",
        f"<h3>Resolved weeks</h3>{craft_closed}" if craft_closed else "",
    )
    html = html.replace("__LUXURY_SCORE__", luxury_scoreboard_html(summary))
    open_luxury_positions = _table(
        [
            "Item", "Opened", "Bought", "Sold", "Bid at", "Ask at",
            "Bid now", "Ask now", "Age", "Patience", "Launch risk", "Floor",
        ],
        luxury_open_rows(state["positions"], today, by_id),
    )
    html = html.replace(
        "__LUXURY_OPEN__",
        open_luxury_positions or "<p>No luxury desk position is open right now.</p>",
    )
    closed_luxury_positions = _table(
        [
            "Item", "Opened", "Outcome", "Bought", "Sold", "Front d",
            "Net", "Predicted net", "Realized", "Patience",
        ],
        luxury_rows(state["records"]),
    )
    html = html.replace(
        "__LUXURY_CLOSED__",
        f"<h3>Resolved picks</h3>{closed_luxury_positions}"
        if closed_luxury_positions
        else "",
    )
    t = summary["thesis"]
    if t["completed"]:
        thead = (
            f"{t['completed']} resolved thesis position(s), net {money(t['net'])}, "
            f"median realized {pct(t['med_net_pct'], 1)} of predicted"
        )
    else:
        thead = (
            "No thesis position has resolved yet; a position resolves on a "
            "target hit, its invalidation date, a human status: closed "
            f"override, or the {THESIS_HORIZON_DAYS}-day safety-net horizon, "
            "whichever comes first."
        )
    html = html.replace("__THESIS_META__", thead)
    thesis_open = _table(
        [
            "Item", "Opened", "Bought", "Sold", "Ceiling", "Target",
            "Bid now", "Ask now", "Age", "Launch risk", "Floor",
        ],
        thesis_open_rows(state["positions"], today, by_id),
    )
    html = html.replace(
        "__THESIS_OPEN__", thesis_open or "<p>No thesis position is open right now.</p>"
    )
    closed_thesis_positions = _table(
        [
            "Item", "Opened", "Closed", "Outcome", "Bought", "Sold",
            "Net", "Predicted net", "Realized",
        ],
        thesis_rows(state["records"]),
    )
    html = html.replace(
        "__THESIS_CLOSED__",
        f"<h3>Resolved positions</h3>{closed_thesis_positions}"
        if closed_thesis_positions
        else "",
    )
    k = summary["corner"]
    if k["n"]:
        khead = (
            f"{k['n']} resolved corner(s), {k['unloaded']} fully unloaded, "
            f"median {k['med_top_days']:.0f} day(s) owning the top of the book "
            f"against {k['med_planned_days']:.0f} planned, "
            f"{pct(k['exit_broken_pct'])} ended undercut or resupplied, "
            f"net {money(k['net'])}"
        )
    else:
        khead = (
            "No corner has resolved yet; one resolves when the lot is gone, "
            "when new listings outrun our sales past the abort tripwire, or after "
            f"{CORNER_HORIZON_DAYS} days, whichever comes first."
        )
    html = html.replace("__CORNER_META__", khead)
    corner_open = _table(
        [
            "Item", "Opened", "Sold", "Avg cost", "Relist", "Ceiling from",
            "Top d", "Undercut d", "Net resupply", "Planned", "Launch risk", "Floor",
        ],
        corner_open_rows(state["positions"], today, by_id),
    )
    html = html.replace(
        "__CORNER_OPEN__", corner_open or "<p>No corner is open right now.</p>"
    )
    corner_closed = _table(
        ["Item", "Opened", "Closed", "Outcome", "Sold", "Top d", "Net resupply",
         "Net", "Predicted net", "Realized"],
        corner_rows(state["records"]),
    )
    html = html.replace(
        "__CORNER_CLOSED__",
        f"<h3>Resolved corners</h3>{corner_closed}" if corner_closed else "",
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
    actions_doc = _read(SITE / "actions.json", {}) or {}
    queue = actions_doc.get("queue") or []
    theses = actions_doc.get("theses") or []

    if store is None:
        from collector.s3store import Store

        store = Store(os.environ["S3_BUCKET"])
    if snapshot is None:
        from collector import dw2

        # Widened to the corner screen's own field list: same endpoint, no
        # extra request, and it brings 1d_sell_listed so tick_corner can
        # measure re-supply. Additive for the other five kinds.
        snapshot = dw2.fetch_snapshot(fields=corner._corner_fields(dw2))
    by_id = {it["id"]: it for it in snapshot if it.get("id") is not None}
    today = today or datetime.now(timezone.utc).date()

    craft_points = (store.get_json_gz(CRAFT_KEY) or {}).get("points") or []
    crafts = craft_points[-1]["chains"] if craft_points else []
    luxury_picks = (store.get_json_gz(LUXURY_KEY) or {}).get("picks") or []
    corner_picks = (store.get_json_gz(CORNER_KEY) or {}).get("picks") or []

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

    state, stats = roll(
        state, picks, queue, by_id, today, crafts, luxury_picks, theses, corner_picks
    )
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
    (SITE / "track.html").write_text(
        render(state, summary, generated, by_id), encoding="utf-8"
    )

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
.o-target_hit { color: #7ec97f; } .o-invalidated { color: #d97a7a; }
.o-horizon_expired { color: #8a8f9c; } .o-closed_by_human { color: #8ab4f8; }
.o-unloaded { color: #7ec97f; } .o-undercut { color: #d97a7a; }
.o-resupplied { color: #d97a7a; }
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
&middot; <a href="invest.html">benchmarks</a>
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
<h2>Time-gated craft carry</h2>
<div class="meta">__CRAFT_META__</div>
<h3>Open positions</h3>
__CRAFT_OPEN__
__CRAFT_CLOSED__
<div class="notes">
<p>A craft-carry position opens the day a refinement's spread clears the
15% tax hurdle (invest.craft, docs/INVESTING.md's S8) and resolves after a
week, matching its "sell weekly" cadence. Crafted counts the days that week
the refinement still priced a spread and so was actually crafted, out of
days observed; a day it stops pricing is simply skipped, not guessed.</p>
</div>
<h2>Luxury desk</h2>
__LUXURY_SCORE__
<h3>Open positions</h3>
__LUXURY_OPEN__
__LUXURY_CLOSED__
<div class="notes">
<p>A luxury desk pick (docs/INVESTING.md strategy S4) is opened the day it
publishes: a patient bid fills only on days the market's best bid comes down
to our price, and the relist mirrors it against the best ask, at the same
capture assumption as the flip table above but over a much longer horizon.
Patience marks a position that has sat unfilled past the three-week rule
that strategy states (invest.luxury.PATIENCE_DAYS); the daily snapshot
carries no order book to actually reprice against mid-simulation, so this
flags the rule firing rather than simulating the repriced fill.</p>
</div>
<h2>Thesis registry</h2>
<div class="meta">__THESIS_META__</div>
<h3>Open positions</h3>
__THESIS_OPEN__
__THESIS_CLOSED__
<div class="notes">
<p>A thesis position (docs/INVESTING.md strategy S2, invest/theses.yaml)
opens the day a registry entry's live price sits at or under its entry
ceiling, and simulates the same buy-order/sell-listing fill mechanic as
the flip table above. It resolves on a target hit, past the registry's own
invalidation date, on a human status: closed override recorded in the
registry (outcome closed_by_human, distinct from a model-driven
resolution), or after a 45-day safety-net horizon, whichever comes first.
Launch risk flags a farmable-class position (T6, festival, staples) with
an expansion launch inside the next 30 days. Floor shows how much of the
position's liquidation mark a structural floor underwrites (strategy S7);
it is reported, never a filter.</p>
</div>
<h2>Corners</h2>
<div class="meta">__CORNER_META__</div>
<h3>Open corners</h3>
__CORNER_OPEN__
__CORNER_CLOSED__
<div class="notes">
<p>A corner (docs/INVESTING.md strategy S6) fills its whole lot at open, at
the ladder VWAP the screen computed off a book that was really observed: a
sweep is one instant action, and the daily snapshot carries no ladder to
simulate it against, so inventing a fill mechanic for the entry would add
fake precision to the leg that is not in doubt. The exit is where the
fidelity belongs. On a day the best ask still sits at or above our relist
nobody has undercut us, so we sell our own planned share of that day's
demand; on a day it sits below we sell nothing and the day counts as
undercut. New sell listings accumulate as re-supply, and once they have
outrun our own sales by one week of the planned schedule, with units still
unsold, the moat is gone and the remainder is dumped at the bid. Net of our
sales on purpose: measured gross, the tripwire would be a clock, since any
refill rate reaches a fixed quantity eventually and a position would abort
on elapsed time rather than on the moat breaking.</p>
<p>The honest limitation: the tracker ticks against a market where we did
not actually buy, so the book we "removed" is still sitting there in the
data. Measuring re-supply as accumulated new listings rather than as
resting depth is the least-wrong option available, and even so it cannot
tell a listing placed because of our raised price from one that would have
arrived anyway.</p>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    main()
