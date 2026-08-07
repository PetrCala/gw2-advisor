"""Build the daily flip report into site/.

Usage:
    python -m report.build            # prices from our S3 state (needs AWS creds)
    python -m report.build --local    # datawars2 prices only, no AWS needed

The datawars2 snapshot supplies names and rolling flow aggregates; our own
collector state overwrites prices and queue sizes when available (at most
~10 minutes stale, against up to an hour for the mirror).
"""

import csv
import json
import os
import sys
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path

from collector import dw2, gw2api
from features import reprice
from scorers import flip
from season import actions as season_actions
from season import score as season_score

SITE = Path(__file__).resolve().parent.parent / "site"

CSV_COLS = [
    "id",
    "name",
    "rarity",
    "buy_at",
    "sell_at",
    "margin",
    "margin_pct",
    "qty",
    "capital",
    "buy_flow",
    "sell_flow",
    "round_trip_days",
    "ev_day",
    "outbid_day",
    "undercut_day",
    "exit_pct",
    "confidence",
]

SEASONAL_CSV_COLS = [
    "id",
    "name",
    "rarity",
    "event",
    "verdict",
    "bucket",
    "buy_window",
    "sell_window",
    "hold_days",
    "days_to_buy",
    "days_left",
    "n_cycles",
    "med_ret",
    "hit_rate",
    "worst_ret",
    "last_ret",
    "strength",
    "cur_price",
    "limit_price",
    "suggested_qty",
    "entry_premium",
    "window_flow",
    "confidence",
    "score",
    "cycles",
]

# The published queue contract; report/actions.py and the notification
# diff both consume site/actions.json, so field changes ripple there.
ACTIONS_COLS = [
    "id",
    "name",
    "event",
    "bucket",
    "verdict",
    "buy_opens",
    "buy_closes",
    "sell_opens",
    "sell_closes",
    "limit_price",
    "cur_price",
    "entry_premium",
    "suggested_qty",
    "days_to_buy",
    "days_left",
    "confidence",
    "score",
    "med_ret",
    "hit_rate",
    "hold_days",
]

SEASONAL_ASSUMPTIONS = {
    "decomposition": "STL (period 52, robust) on weekly log sell prices over "
    "up to 8 years of daily history, refreshed weekly",
    "windows": "buy window brackets the seasonal trough, sell window the peak; "
    "event-linked items track each year's actual festival dates, including the "
    "upcoming window once the run is announced (typical dates until then)",
    "returns": "per cycle: buy at the median daily price inside the buy window, "
    "sell at the median inside the sell window, net of 15% fees; cycles missing "
    "observed prices in either window are dropped, not guessed",
    "filters": f"at least {season_score.MIN_CYCLES} completed cycles, "
    f"median return {season_score.MIN_MEDIAN_RET:.0%}, "
    f"hit rate {season_score.MIN_HIT_RATE:.0%}, "
    f"seasonal strength {season_score.MIN_STRENGTH:g} or better",
    "ranking": "score = median return times hit rate, damped below 6 cycles; "
    "high confidence needs 6+ cycles, an 80% hit rate and a positive latest cycle",
    "timing": "verdicts and prices recomputed at every report build; pay-up-to "
    f"= recent-{season_actions.RECENT_CYCLES}-cycle median sell price x 85% / "
    f"(1 + {season_actions.RETURN_HURDLE:.0%} hurdle); suggested qty = "
    f"{season_actions.CAPTURE:.0%} of the buy-window flow over its remaining "
    f"days, capped at {season_actions.SEASON_CAPITAL // 10000}g per pick",
}

ASSUMPTIONS = {
    "placement": "prices walked into order-book gaps, accepting at most "
    f"{flip.WAIT_TOLERANCE_DAYS:g} days of queue ahead of us per side",
    "fees": "15% of sale (5% listing, 10% tax)",
    "capture": f"{flip.CAPTURE:.0%} of each side's 7-day average filled flow "
    "once our order reaches the front",
    "lot_size": f"capped by {flip.CAPITAL_PER_ITEM // 10000}g capital per item "
    f"and a {flip.MAX_ROUND_TRIP_DAYS:g}-day round trip including queue wait",
    "filters": f"margin at least {flip.MIN_MARGIN_PCT:.0%} of cost, "
    f"at least {flip.MIN_FLOW:g} units/day filled on both sides, "
    f"items repriced more than {flip.PENNY_WAR_PER_DAY:g} times/day dropped "
    "as penny wars",
    "exit": "exit % is the loss per unit if the whole lot were dumped into "
    "the current buy book right after purchase",
}


def main():
    local = "--local" in sys.argv
    snap = dw2.fetch_snapshot()
    price_ts = None
    if not local:
        from collector.s3store import Store

        store = Store(os.environ["S3_BUCKET"])
        state = store.get_json_gz("state/latest.json.gz")
        prices = {int(k): v for k, v in state["items"].items()}
        price_ts = state["ts"]
        for it in snap:
            p = prices.get(it.get("id"))
            if p:
                it["buy_price"], it["buy_quantity"], it["sell_price"], it["sell_quantity"] = p

    shortlist = flip.score_all(snap, top_n=flip.SHORTLIST_N)
    books = gw2api.fetch_listings([p["id"] for p in shortlist])

    rates = None
    if not local:
        try:
            rows, span = reprice.load_events(store)
            if rows:
                rates = reprice.reprice_rates(rows, span)
        except Exception as e:  # rates refine the output, they don't gate it
            print(f"reprice rates unavailable: {e}")

    picks = []
    for p in shortlist:
        b = books.get(p["id"])
        if not b:
            continue
        r = flip.rescore(p, b, rates.get(p["id"], (0.0, 0.0)) if rates else None)
        if r:
            picks.append(r)
    picks.sort(key=lambda s: s["ev_day"], reverse=True)
    picks = picks[: flip.TOP_N]

    seasonal = None
    if not local:
        try:  # seasonal picks refine the page, they don't gate it
            seasonal = store.get_json_gz("season/latest.json.gz")
        except Exception as e:
            print(f"seasonal picks unavailable: {e}")

    festivals = []
    if seasonal:
        # The artifact's calendar and price fields go up to a week stale
        # between season runs; recompute them against today's snapshot.
        fresh = {it.get("id"): it.get("sell_price") for it in snap}
        today = datetime.now(timezone.utc).date()
        for p in seasonal["picks"]:
            season_actions.enrich(p, today, fresh.get(p["id"]))
        festivals = season_actions.next_festivals(today, seasonal["picks"])

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    write_site(
        picks, scanned=len(snap), generated=generated, price_ts=price_ts,
        seasonal=seasonal, festivals=festivals,
    )
    n_seasonal = len(seasonal["picks"]) if seasonal else 0
    print(
        f"{len(picks)} picks from {len(shortlist)} shortlisted of {len(snap)} items "
        f"(reprice rates: {'yes' if rates else 'no'}, "
        f"seasonal picks: {n_seasonal}) -> {SITE / 'index.html'}"
    )


def money_html(c):
    """Copper as the same colored g/s/c spans the JS table renders."""
    c = int(round(c))
    g, s, k = c // 10000, c % 10000 // 100, c % 100
    out = ""
    if g:
        out += f'<span class="g">{g}g</span>&thinsp;'
    if g or s:
        out += f'<span class="s">{s}s</span>&thinsp;'
    return out + f'<span class="c">{k}c</span>'


def _mmdd(iso):
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day:02d}"


def _item_link(p):
    return (
        f'<a class="r-{escape(p["rarity"])}" '
        f'href="https://www.gw2bltc.com/en/item/{p["id"]}">'
        f"{escape(p['name'])}</a>"
    )


def queue_html(spicks):
    """The action queue panel: today's answers, ahead of the full table."""
    groups = {b: [] for b in season_actions.BUCKETS}
    for p in spicks:
        groups[p["bucket"]].append(p)
    parts = []

    buy = groups["buy_now"]
    parts.append(f"<h3>Buy now ({len(buy)})</h3>")
    if buy:
        lines = []
        for p in buy:
            qty = f", qty {p['suggested_qty']:,}" if p["suggested_qty"] else ""
            lines.append(
                f"<li>{_item_link(p)}: pay up to {money_html(p['limit_price'])} "
                f"(now {money_html(p['cur_price'])}){qty}, "
                f"closes {_mmdd(p['buy_closes'])}</li>"
            )
        parts.append("<ul>" + "".join(lines) + "</ul>")
    else:
        parts.append("<p>Nothing under its ceiling today.</p>")

    soon = groups["opens_soon"]
    if soon:
        parts.append(f"<h3>Opens soon ({len(soon)})</h3><ul>")
        for p in soon:
            parts.append(
                f"<li>{_item_link(p)}: opens {_mmdd(p['buy_opens'])} "
                f"({p['days_to_buy']}d), pay up to {money_html(p['limit_price'])}</li>"
            )
        parts.append("</ul>")

    selling = groups["sell_active"]
    if selling:
        parts.append(f"<h3>Sell window ({len(selling)})</h3><ul>")
        for p in selling:
            parts.append(
                f"<li>{_item_link(p)}: sell window closes "
                f"{_mmdd(p['sell_closes'])}</li>"
            )
        parts.append("</ul>")

    dormant = len(groups["dormant"])
    if dormant:
        parts.append(
            f"<p>{dormant} picks dormant (window months away, or the entry "
            "already gone); the table below has every row.</p>"
        )
    return '<div class="queue">' + "".join(parts) + "</div>"


def festivals_html(festivals, today):
    bits = []
    for f in festivals:
        d0 = date.fromisoformat(f["start"])
        when = "running" if d0 <= today else f"in {(d0 - today).days}d"
        est = ", est." if f["estimated"] else ""
        picks = f", {f['picks']} picks" if f["picks"] else ""
        bits.append(
            f"<b>{escape(f['name'])}</b> {_mmdd(f['start'])} - "
            f"{_mmdd(f['end'])} ({when}{est}{picks})"
        )
    return " &middot; ".join(bits)


def write_site(picks, scanned, generated, price_ts, seasonal=None, festivals=()):
    SITE.mkdir(exist_ok=True)

    payload = {
        "generated": generated,
        "price_ts": price_ts,
        "scanned": scanned,
        "assumptions": ASSUMPTIONS,
        "picks": picks,
        "seasonal": seasonal,
    }
    (SITE / "data.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")

    with open(SITE / "data.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        for p in picks:
            w.writerow({k: p[k] for k in CSV_COLS})

    spicks = (seasonal or {}).get("picks") or []
    with open(SITE / "seasonal.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SEASONAL_CSV_COLS)
        w.writeheader()
        for p in spicks:
            row = {k: p.get(k) for k in SEASONAL_CSV_COLS if k != "cycles"}
            row["cycles"] = season_score.fmt_cycles(p["cycles"])
            w.writerow(row)

    actions_payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "queue": [{k: p.get(k) for k in ACTIONS_COLS} for p in spicks],
        "festivals": list(festivals),
    }
    (SITE / "actions.json").write_text(
        json.dumps(actions_payload, indent=1), encoding="utf-8"
    )

    if seasonal:
        smeta = (
            f"computed {seasonal['generated'][:10]} from history through "
            f"{seasonal['history_through']} &middot; {len(spicks)} picks of "
            f"{seasonal['items_decomposed']:,} decomposed"
        )
    else:
        smeta = "no seasonal artifact yet; the weekly season workflow fills this in"

    today = datetime.now(timezone.utc).date()
    html = TEMPLATE
    html = html.replace("__ROWS__", json.dumps(picks))
    html = html.replace("__SEASONAL_ROWS__", json.dumps(spicks))
    html = html.replace("__QUEUE__", queue_html(spicks) if spicks else "")
    html = html.replace(
        "__FESTIVALS__", festivals_html(festivals, today) if festivals else ""
    )
    html = html.replace("__SEASONAL_META__", smeta)
    html = html.replace("__GENERATED__", generated)
    html = html.replace("__PRICES__", price_ts or "datawars2 mirror (up to ~1h old)")
    html = html.replace("__SCANNED__", f"{scanned:,}")
    assumptions = "".join(f"<li>{v}</li>" for v in ASSUMPTIONS.values())
    html = html.replace("__ASSUMPTIONS__", assumptions)
    sassumptions = "".join(f"<li>{v}</li>" for v in SEASONAL_ASSUMPTIONS.values())
    html = html.replace("__SEASONAL_ASSUMPTIONS__", sassumptions)
    (SITE / "index.html").write_text(html, encoding="utf-8")


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gw2-advisor: daily flips</title>
<style>
:root { color-scheme: dark; }
body { background: #16181d; color: #d6d8de; font: 14px/1.5 system-ui, sans-serif;
       margin: 0 auto; max-width: 1080px; padding: 24px 16px 48px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 36px 0 4px; }
.meta { color: #8a8f9c; font-size: 13px; margin-bottom: 16px; }
.meta a { color: #8ab4f8; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 5px 8px; text-align: right; white-space: nowrap; }
th { cursor: pointer; user-select: none; color: #aab0bd; border-bottom: 1px solid #3a3f4b;
     position: sticky; top: 0; background: #16181d; }
th.active { color: #fff; }
td:nth-child(2), th:nth-child(2) { text-align: left; }
#t2 td:nth-child(3), #t2 th:nth-child(3), #t2 td:nth-child(4), #t2 th:nth-child(4),
#t2 td:nth-child(5), #t2 th:nth-child(5), #t2 td:nth-child(6), #t2 th:nth-child(6)
{ text-align: left; }
tbody tr:nth-child(odd) { background: #1b1e25; }
tbody tr:hover { background: #232733; }
td a { color: inherit; text-decoration: none; }
td a:hover { text-decoration: underline; }
.g { color: #e5c07b; } .s { color: #b4b9c4; } .c { color: #b87352; }
.conf-high { color: #7ec97f; } .conf-medium { color: #e0c068; } .conf-low { color: #a0a5b1; }
.v-buy_now { color: #7ec97f; } .v-opens_soon { color: #e0c068; }
.v-sell_active { color: #8ab4f8; } .v-dormant { color: #8a8f9c; }
.queue { background: #1b1e25; border: 1px solid #3a3f4b; border-radius: 6px;
         padding: 4px 16px 10px; margin: 12px 0 20px; }
.queue h3 { font-size: 14px; margin: 10px 0 2px; color: #aab0bd; }
.queue ul { margin: 4px 0; padding-left: 20px; }
.queue p { margin: 6px 0; color: #8a8f9c; }
.r-Fine { color: #62a4da; } .r-Masterwork { color: #59b135; } .r-Rare { color: #fcd00b; }
.r-Exotic { color: #ffa405; } .r-Ascended { color: #fb3e8d; } .r-Legendary { color: #a675f0; }
.notes { color: #8a8f9c; font-size: 13px; margin-top: 24px; }
.notes ul { margin: 6px 0; padding-left: 20px; }
</style>
</head>
<body>
<h1>gw2-advisor: daily flips</h1>
<div class="meta">
Generated __GENERATED__ &middot; prices as of __PRICES__ &middot; __SCANNED__ items scanned
&middot; <a href="data.csv">csv</a> &middot; <a href="data.json">json</a>
&middot; <a href="https://github.com/PetrCala/gw2-advisor">source</a>
</div>
<table id="t">
<thead><tr></tr></thead>
<tbody></tbody>
</table>
<div class="notes">
<p>Ranked by EV/day: after-fee margin over the estimated per-unit round-trip
time at our assumed share of traded flow. Model assumptions:</p>
<ul>__ASSUMPTIONS__</ul>
<p>Estimates from public data; spreads can close before you act. Check the
live order book in game before committing gold. Item links go to gw2bltc
for a second opinion.</p>
</div>
<h2>Speculative seasonal picks</h2>
<div class="meta">
__SEASONAL_META__ &middot; <a href="seasonal.csv">csv</a>
&middot; <a href="actions.json">actions.json</a>
</div>
<div class="meta">__FESTIVALS__</div>
__QUEUE__
<table id="t2">
<thead><tr></tr></thead>
<tbody></tbody>
</table>
<div class="notes">
<p>Buy-and-hold candidates from multi-year seasonality, sorted by what is
actionable today (click Score to rank by pattern quality instead). The
timing column is the verdict: buy now means the current price sits under
the pay-up-to ceiling inside the buy window. The cycles column counts
positive past cycles; hover it for every year's return. Model
assumptions:</p>
<ul>__SEASONAL_ASSUMPTIONS__</ul>
<p>Speculative by nature: capital sits locked for weeks, and expansion
launches reprice whole material classes (cycles overlapping one are
labeled in the hover). Few cycles mean wide error bars; treat low-cycle
rows as anecdotes, not patterns.</p>
</div>
<script>
var cols = [
  {key: "name", label: "Item", str: true},
  {key: "buy_at", label: "Buy at", money: true},
  {key: "sell_at", label: "Sell at", money: true},
  {key: "margin", label: "Margin", money: true},
  {key: "margin_pct", label: "Margin %", pct: true},
  {key: "qty", label: "Qty"},
  {key: "capital", label: "Capital", money: true},
  {key: "buy_flow", label: "Buys/d"},
  {key: "sell_flow", label: "Sells/d"},
  {key: "outbid_day", label: "Outbid/d"},
  {key: "undercut_day", label: "Under/d"},
  {key: "exit_pct", label: "Exit", pct: true},
  {key: "round_trip_days", label: "Round trip", days: true},
  {key: "ev_day", label: "EV/day", money: true},
  {key: "confidence", label: "Conf", str: true}
];
var scols = [
  {key: "name", label: "Item", str: true},
  {key: "event", label: "Event", str: true},
  {key: "verdict", label: "Timing", str: true},
  {key: "buy_window", label: "Buy", str: true},
  {key: "sell_window", label: "Sell", str: true},
  {key: "n_cycles", label: "Cycles"},
  {key: "med_ret", label: "Med ret", pct: true},
  {key: "worst_ret", label: "Worst", pct: true},
  {key: "last_ret", label: "Last", pct: true},
  {key: "hit_rate", label: "Hit", pct: true},
  {key: "hold_days", label: "Hold d"},
  {key: "limit_price", label: "Pay up to", money: true},
  {key: "cur_price", label: "Price", money: true},
  {key: "suggested_qty", label: "Qty"},
  {key: "window_flow", label: "Flow/d"},
  {key: "confidence", label: "Conf", str: true},
  {key: "score", label: "Score"}
];

function esc(s) {
  return String(s).replace(/[&<>"]/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c];
  });
}
function money(c) {
  c = Math.round(c);
  var g = Math.floor(c / 10000), s = Math.floor(c % 10000 / 100), k = c % 100, out = "";
  if (g) out += '<span class="g">' + g + "g</span>&thinsp;";
  if (g || s) out += '<span class="s">' + s + "s</span>&thinsp;";
  return out + '<span class="c">' + k + "c</span>";
}
function cell(r, c) {
  var v = r[c.key];
  if (v === null || v === undefined) return "";
  if (c.key === "name")
    return '<a class="r-' + esc(r.rarity) + '" href="https://www.gw2bltc.com/en/item/' +
      r.id + '" title="' + esc(r.rarity) + '">' + esc(v) + "</a>";
  if (c.key === "confidence")
    return '<span class="conf-' + esc(v) + '">' + esc(v) + "</span>";
  if (c.key === "verdict")
    return '<span class="v-' + esc(r.bucket) + '">' + esc(v) + "</span>";
  if (c.key === "n_cycles" && r.cycles) {
    var up = 0, tip = [];
    r.cycles.forEach(function (x) {
      if (x.ret > 0) up++;
      tip.push(x.year + " " + (x.ret >= 0 ? "+" : "") + Math.round(100 * x.ret) +
        "%" + (x.release ? " (" + x.release + ")" : ""));
    });
    return '<span title="' + esc(tip.join(", ")) + '">' + up + "/" + v + " up</span>";
  }
  if (c.money) return money(v);
  if (c.pct) return (100 * v).toFixed(1) + "%";
  if (c.days) return v < 1 ? Math.round(v * 24) + "h" : v.toFixed(1) + "d";
  return v.toLocaleString();
}
function makeTable(id, rows, cols, sortKey) {
  var sortAsc = false;
  var table = document.getElementById(id);
  function render() {
    var sorted = rows.slice().sort(function (a, b) {
      var x = a[sortKey], y = b[sortKey];
      if (x === null || x === undefined) x = -Infinity;
      if (y === null || y === undefined) y = -Infinity;
      var d = typeof x === "string" ? x.localeCompare(y) : x - y;
      return sortAsc ? d : -d;
    });
    var head = "<th>#</th>" + cols.map(function (c) {
      var cls = c.key === sortKey ? ' class="active"' : "";
      var arrow = c.key === sortKey ? (sortAsc ? " \\u2191" : " \\u2193") : "";
      return "<th" + cls + ' data-key="' + c.key + '">' + c.label + arrow + "</th>";
    }).join("");
    table.querySelector("thead tr").innerHTML = head;
    table.querySelector("tbody").innerHTML = sorted.map(function (r, i) {
      return "<tr><td>" + (i + 1) + "</td>" + cols.map(function (c) {
        return "<td>" + cell(r, c) + "</td>";
      }).join("") + "</tr>";
    }).join("");
    table.querySelectorAll("th[data-key]").forEach(function (th) {
      th.onclick = function () {
        var k = th.getAttribute("data-key");
        if (k === sortKey) sortAsc = !sortAsc;
        else { sortKey = k; sortAsc = false; }
        render();
      };
    });
  }
  render();
}
makeTable("t", __ROWS__, cols, "ev_day");
var srows = __SEASONAL_ROWS__;
if (srows.length) makeTable("t2", srows, scols, "act");
else document.getElementById("t2").style.display = "none";
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
