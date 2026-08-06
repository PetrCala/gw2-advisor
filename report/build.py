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
from datetime import datetime, timezone
from pathlib import Path

from collector import dw2
from scorers import flip

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
    "confidence",
]

ASSUMPTIONS = {
    "placement": "buy order 1c above best buy, listing 1c under best sell",
    "fees": "15% of sale (5% listing, 10% tax)",
    "capture": f"{flip.CAPTURE:.0%} of each side's 7-day average filled flow",
    "lot_size": f"capped by {flip.CAPITAL_PER_ITEM // 10000}g capital per item "
    f"and a {flip.MAX_ROUND_TRIP_DAYS:g}-day round trip",
    "filters": f"margin at least {flip.MIN_MARGIN_PCT:.0%} of cost, "
    f"at least {flip.MIN_FLOW:g} units/day filled on both sides",
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

    picks = flip.score_all(snap)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    write_site(picks, scanned=len(snap), generated=generated, price_ts=price_ts)
    print(f"{len(picks)} picks from {len(snap)} items -> {SITE / 'index.html'}")


def write_site(picks, scanned, generated, price_ts):
    SITE.mkdir(exist_ok=True)

    payload = {
        "generated": generated,
        "price_ts": price_ts,
        "scanned": scanned,
        "assumptions": ASSUMPTIONS,
        "picks": picks,
    }
    (SITE / "data.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")

    with open(SITE / "data.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        for p in picks:
            w.writerow({k: p[k] for k in CSV_COLS})

    html = TEMPLATE
    html = html.replace("__ROWS__", json.dumps(picks))
    html = html.replace("__GENERATED__", generated)
    html = html.replace("__PRICES__", price_ts or "datawars2 mirror (up to ~1h old)")
    html = html.replace("__SCANNED__", f"{scanned:,}")
    assumptions = "".join(f"<li>{v}</li>" for v in ASSUMPTIONS.values())
    html = html.replace("__ASSUMPTIONS__", assumptions)
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
.meta { color: #8a8f9c; font-size: 13px; margin-bottom: 16px; }
.meta a { color: #8ab4f8; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 5px 8px; text-align: right; white-space: nowrap; }
th { cursor: pointer; user-select: none; color: #aab0bd; border-bottom: 1px solid #3a3f4b;
     position: sticky; top: 0; background: #16181d; }
th.active { color: #fff; }
td:nth-child(2), th:nth-child(2) { text-align: left; }
tbody tr:nth-child(odd) { background: #1b1e25; }
tbody tr:hover { background: #232733; }
td a { color: inherit; text-decoration: none; }
td a:hover { text-decoration: underline; }
.g { color: #e5c07b; } .s { color: #b4b9c4; } .c { color: #b87352; }
.conf-high { color: #7ec97f; } .conf-medium { color: #e0c068; } .conf-low { color: #a0a5b1; }
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
<script>
var rows = __ROWS__;
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
  {key: "round_trip_days", label: "Round trip", days: true},
  {key: "ev_day", label: "EV/day", money: true},
  {key: "confidence", label: "Conf", str: true}
];
var sortKey = "ev_day", sortAsc = false;

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
  if (c.key === "name")
    return '<a class="r-' + esc(r.rarity) + '" href="https://www.gw2bltc.com/en/item/' +
      r.id + '" title="' + esc(r.rarity) + '">' + esc(v) + "</a>";
  if (c.key === "confidence")
    return '<span class="conf-' + esc(v) + '">' + esc(v) + "</span>";
  if (c.money) return money(v);
  if (c.pct) return (100 * v).toFixed(1) + "%";
  if (c.days) return v < 1 ? Math.round(v * 24) + "h" : v.toFixed(1) + "d";
  return v.toLocaleString();
}
function render() {
  var sorted = rows.slice().sort(function (a, b) {
    var x = a[sortKey], y = b[sortKey];
    var d = typeof x === "string" ? x.localeCompare(y) : x - y;
    return sortAsc ? d : -d;
  });
  var head = "<th>#</th>" + cols.map(function (c) {
    var cls = c.key === sortKey ? ' class="active"' : "";
    var arrow = c.key === sortKey ? (sortAsc ? " \\u2191" : " \\u2193") : "";
    return "<th" + cls + ' data-key="' + c.key + '">' + c.label + arrow + "</th>";
  }).join("");
  document.querySelector("thead tr").innerHTML = head;
  document.querySelector("tbody").innerHTML = sorted.map(function (r, i) {
    return "<tr><td>" + (i + 1) + "</td>" + cols.map(function (c) {
      return "<td>" + cell(r, c) + "</td>";
    }).join("") + "</tr>";
  }).join("");
  document.querySelectorAll("th[data-key]").forEach(function (th) {
    th.onclick = function () {
      var k = th.getAttribute("data-key");
      if (k === sortKey) sortAsc = !sortAsc;
      else { sortKey = k; sortAsc = false; }
      render();
    };
  });
}
render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
