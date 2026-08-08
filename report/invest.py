"""Build the benchmarks page into site/invest.html.

Usage:
    python -m report.invest             # reads the invest/ artifacts from S3
    python -m report.invest --local     # renders the empty-state page, no AWS

Reads the weekly index artifact (invest/bench.json.gz) and the daily NAV
series (invest/nav.json.gz) and renders the "are we ahead of the market"
page: benchmark lines (index, raw gold, ecto, gem rate), the NAV line, the
allocation caps, and the holdings class breakdown.

Privacy rule: the NAV series is stored in the private bucket in absolute
gold, but everything published here is rebased to 100 or expressed as a
percentage, so the public page never carries absolute wealth. The bankroll
(BANKROLL_G) is used only as a denominator for the same reason.
"""

import json
import os
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from invest import bankroll, tags
from invest.index import BENCH_KEY, trailing_return
from invest.nav import NAV_KEY

SITE = Path(__file__).resolve().parent.parent / "site"

WINDOWS = (("30d", 30), ("90d", 90), ("365d", 365), ("all", 36500))

CLASS_LABELS = {
    "t6": "T6 fine mats",
    "festival": "festival",
    "staples": "staples",
    "luxury": "luxury (500g+)",
    "other": "everything else",
}


def rebase(values, base=100.0):
    """Series scaled so its first usable value is `base`."""
    first = next((v for v in values if v), None)
    if not first:
        return values
    return [round(v / first * base, 3) if v else None for v in values]


def nav_public(nav):
    """The publishable slice of the NAV series: shapes and shares, no gold."""
    points = (nav or {}).get("points") or []
    if not points:
        return None
    dates = [p["d"] for p in points]
    floors = [p["floor"] for p in points]
    latest = points[-1]
    stock = latest.get("stock_floor") or 0
    total = latest.get("floor") or 0
    classes = latest.get("classes") or {}
    composition = [
        {
            "cls": cls,
            "label": CLASS_LABELS.get(cls, cls),
            "stock_share": round(classes.get(cls, 0) / stock, 4) if stock else None,
            "nav_share": round(classes.get(cls, 0) / total, 4) if total else None,
        }
        for cls in tags.CLASSES
    ]
    returns = {
        label: trailing_return(dates, floors, days, dates[-1])
        for label, days in WINDOWS
    }
    return {
        "dates": dates,
        "levels": rebase(floors),
        "since": dates[0],
        "points": len(points),
        "items": latest.get("items"),
        "liquid_share": round(latest["liquid"] / total, 4) if total else None,
        "mark_premium": round(latest["mark"] / latest["floor"] - 1, 4)
        if latest.get("floor")
        else None,
        "composition": composition,
        "returns": returns,
    }


def exposure_rows(nav, bankroll_copper):
    """Class exposure as shares of stock and, when configured, of bankroll."""
    points = (nav or {}).get("points") or []
    if not points:
        return []
    latest = points[-1]
    classes = latest.get("classes") or {}
    stock = latest.get("stock_floor") or 0
    rows = []
    for cls in tags.CLASSES:
        v = classes.get(cls, 0)
        rows.append(
            {
                "cls": cls,
                "label": CLASS_LABELS.get(cls, cls),
                "stock_share": round(v / stock, 4) if stock else None,
                "bankroll_share": round(v / bankroll_copper, 4)
                if bankroll_copper
                else None,
            }
        )
    return rows


def build_payload(bench, nav, bankroll_copper):
    nav_pub = nav_public(nav)
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "bench": {
            "generated": bench.get("generated"),
            "history_through": bench.get("history_through"),
            "params": bench.get("params"),
            "index": bench.get("index"),
            "ecto": bench.get("ecto"),
            "gems": bench.get("gems"),
            "constituents": bench.get("constituents"),
        }
        if bench
        else None,
        "nav": nav_pub,
        "caps": [
            {"strategy": r["strategy"], "cap_pct": r["cap_pct"]}
            for r in bankroll.cap_table()
        ],
        "bankroll_set": bool(bankroll_copper),
        "exposure": exposure_rows(nav, bankroll_copper),
    }


def pct(v, digits=1):
    return "" if v is None else f"{100 * v:+.{digits}f}%"


def share(v, digits=1):
    return "" if v is None else f"{100 * v:.{digits}f}%"


def returns_table(payload):
    """Series x window return table, from the artifacts' own numbers."""
    rows = []
    bench = payload.get("bench") or {}
    for label, series in (
        ("Market index (top 500)", bench.get("index")),
        ("Glob of Ectoplasm", bench.get("ecto")),
        ("Gems (gold per 100)", bench.get("gems")),
    ):
        if series and series.get("returns"):
            r = series["returns"]
            rows.append((label, r.get("30d"), r.get("90d"), r.get("365d"), r.get("all")))
    nav = payload.get("nav")
    if nav:
        r = nav["returns"]
        rows.append(("Portfolio NAV", r.get("30d"), r.get("90d"), r.get("365d"), r.get("all")))
    rows.append(("Raw gold", 0.0, 0.0, 0.0, 0.0))
    body = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            escape(name), pct(a), pct(b), pct(c), pct(d)
        )
        for name, a, b, c, d in rows
    )
    return (
        "<table><thead><tr><th>Series</th><th>30d</th><th>90d</th>"
        f"<th>365d</th><th>All</th></tr></thead><tbody>{body}</tbody></table>"
    )


def caps_table(payload):
    rows = "".join(
        f"<tr><td>{escape(r['strategy'])}</td><td>{share(r['cap_pct'], 0)}</td></tr>"
        for r in payload["caps"]
    )
    note = (
        ""
        if payload["bankroll_set"]
        else "<p>Set the BANKROLL_G repository variable (whole gold) to size "
        "these caps and the exposure shares against your bankroll.</p>"
    )
    return (
        "<table><thead><tr><th>Strategy</th><th>Cap, share of bankroll</th></tr>"
        f"</thead><tbody>{rows}</tbody></table>"
        "<p>Caps bind at entry time once strategies emit positions (M6.b "
        "onward); flips are uncapped because their capital is self-limiting."
        f"</p>{note}"
    )


def exposure_table(payload):
    rows = payload["exposure"]
    if not rows:
        return (
            "<p>No NAV series yet. Add a GW2_API_KEY repository secret (scopes: "
            "account, wallet, inventories, characters, tradingpost) and the "
            "daily report starts appending one point per day.</p>"
        )
    cells = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            escape(r["label"]), share(r["stock_share"]), share(r["bankroll_share"])
        )
        for r in rows
    )
    nav = payload["nav"]
    meta = []
    if nav:
        if nav.get("liquid_share") is not None:
            meta.append(f"liquid (coin + escrow) {share(nav['liquid_share'])} of NAV")
        if nav.get("items") is not None:
            meta.append(f"{nav['items']:,} distinct items held")
        if nav.get("mark_premium") is not None:
            meta.append(f"patient mark {pct(nav['mark_premium'])} above the floor")
    return (
        "<table><thead><tr><th>Class</th><th>Share of stock</th>"
        f"<th>Share of bankroll</th></tr></thead><tbody>{cells}</tbody></table>"
        + (f"<p>{escape('; '.join(meta))}.</p>" if meta else "")
    )


def constituents_table(payload):
    bench = payload.get("bench") or {}
    rows = bench.get("constituents") or []
    if not rows:
        return ""
    cells = "".join(
        "<tr><td><a href=\"https://www.gw2bltc.com/en/item/{}\">{}</a></td>"
        "<td>{}</td><td>{:,}g</td></tr>".format(
            r["id"], escape(r["name"]), share(r["weight"]), r["turnover_g_day"]
        )
        for r in rows
    )
    return (
        "<h3>Largest index constituents</h3>"
        "<table><thead><tr><th>Item</th><th>Weight</th><th>Turnover/day</th>"
        f"</tr></thead><tbody>{cells}</tbody></table>"
    )


def render(payload):
    bench = payload.get("bench")
    if bench and bench.get("index"):
        idx = bench["index"]
        meta = (
            f"index computed {bench['generated'][:10]}, "
            f"{idx['start']} to {bench['history_through']}, "
            f"level {idx['values'][-1]:.1f}"
        )
    else:
        meta = "no benchmark artifact yet; the weekly invest workflow fills this in"
    html = TEMPLATE
    html = html.replace("__GENERATED__", payload["generated"])
    html = html.replace("__META__", meta)
    html = html.replace("__RETURNS__", returns_table(payload))
    html = html.replace("__CAPS__", caps_table(payload))
    html = html.replace("__EXPOSURE__", exposure_table(payload))
    html = html.replace("__CONSTITUENTS__", constituents_table(payload))
    # </ would end the script block early if an item name ever carried it
    html = html.replace("__DATA__", json.dumps(payload).replace("</", "<\\/"))
    return html


def main():
    local = "--local" in sys.argv
    bench = nav = None
    if not local:
        from collector.s3store import Store

        store = Store(os.environ["S3_BUCKET"])
        bench = store.get_json_gz(BENCH_KEY)
        nav = store.get_json_gz(NAV_KEY)

    payload = build_payload(bench, nav, bankroll.bankroll_copper())
    SITE.mkdir(exist_ok=True)
    (SITE / "invest.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    (SITE / "invest.html").write_text(render(payload), encoding="utf-8")
    print(
        f"benchmarks page (index: {'yes' if bench else 'no'}, "
        f"nav points: {len((nav or {}).get('points') or [])}) "
        f"-> {SITE / 'invest.html'}"
    )


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gw2-advisor: benchmarks</title>
<style>
:root { color-scheme: dark; }
body { background: #16181d; color: #d6d8de; font: 14px/1.5 system-ui, sans-serif;
       margin: 0 auto; max-width: 1080px; padding: 24px 16px 48px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 36px 0 4px; }
h3 { font-size: 14px; margin: 24px 0 4px; color: #aab0bd; }
.meta { color: #8a8f9c; font-size: 13px; margin-bottom: 16px; }
.meta a { color: #8ab4f8; }
table { border-collapse: collapse; margin: 8px 0; }
th, td { padding: 5px 10px; text-align: right; white-space: nowrap; }
th { color: #aab0bd; border-bottom: 1px solid #3a3f4b; }
td:first-child, th:first-child { text-align: left; }
tbody tr:nth-child(odd) { background: #1b1e25; }
td a { color: inherit; text-decoration: none; }
td a:hover { text-decoration: underline; }
.chartbox { background: #1b1e25; border: 1px solid #3a3f4b; border-radius: 6px;
            padding: 10px 12px; margin: 10px 0; }
.controls { margin: 4px 0 8px; color: #aab0bd; font-size: 13px; }
.controls button { background: #232733; color: #d6d8de; border: 1px solid #3a3f4b;
                   border-radius: 4px; padding: 2px 10px; margin-right: 4px;
                   cursor: pointer; font: inherit; }
.controls button.active { background: #8ab4f8; color: #16181d; border-color: #8ab4f8; }
.controls label { margin-left: 10px; cursor: pointer; }
.legend span { display: inline-block; margin-right: 14px; font-size: 13px; }
.legend i { display: inline-block; width: 14px; height: 3px; margin-right: 5px;
            vertical-align: middle; border-radius: 2px; }
svg text { fill: #8a8f9c; font-size: 11px; }
svg .grid { stroke: #2a2e38; stroke-width: 1; }
.notes { color: #8a8f9c; font-size: 13px; margin-top: 24px; }
.notes ul { margin: 6px 0; padding-left: 20px; }
</style>
</head>
<body>
<h1>gw2-advisor: benchmarks</h1>
<div class="meta">
Generated __GENERATED__ &middot; __META__
&middot; <a href="invest.json">json</a> &middot; <a href="index.html">report</a>
&middot; <a href="track.html">track record</a>
&middot; <a href="https://github.com/PetrCala/gw2-advisor">source</a>
</div>
<p>"Ahead of the market" is a comparison, so this page is the market: a
turnover-weighted index over the trailing top 500 items, the raw-gold
baseline, ectos, the gem rate, and the portfolio NAV rebased to 100 where
it enters. Every line is what 100 held that way would be worth.</p>
<div class="chartbox">
<div class="controls" id="win"></div>
<div id="chart"></div>
<div class="legend" id="legend"></div>
</div>
<h2>Returns</h2>
__RETURNS__
<h2>Holdings by class</h2>
__EXPOSURE__
<h2>Allocation caps</h2>
__CAPS__
__CONSTITUENTS__
<div class="notes">
<ul>
<li>Index: constituents are the top 500 items by prior-month sell-side
turnover, reweighted monthly, single item capped at 5%; levels chain daily
from item mid prices, renormalized over what actually traded. It starts
mid-2019 because the mirror carries no fills before then.</li>
<li>NAV marks every holding at a dump into the current best bid, net of
the 15% cut; the sell-listing mark is shown only as a premium. Absolute
gold never appears here: the NAV line is rebased to 100 and shares are
percentages.</li>
<li>The gem line is the cost of buying 100 gems with gold (gw2tp history
plus the official exchange spot). It is the closest thing to an outside
inflation gauge, not a tradable series: the round trip loses ~35%.</li>
</ul>
</div>
<script>
var DATA = __DATA__;
var SERIES = [];
(function () {
  var b = DATA.bench || {};
  if (b.index) SERIES.push({name: "Index", color: "#8ab4f8", dates: b.index.dates, values: b.index.values});
  SERIES.push({name: "Raw gold", color: "#8a8f9c", dash: "4 4", flat: 100});
  if (b.ecto) SERIES.push({name: "Ecto", color: "#7ec97f", dates: b.ecto.dates, values: b.ecto.values});
  if (b.gems) SERIES.push({name: "Gems", color: "#a675f0", dates: b.gems.dates, values: b.gems.values});
  if (DATA.nav) SERIES.push({name: "NAV", color: "#ffffff", dates: DATA.nav.dates, values: DATA.nav.levels});
})();

var windows = [["30d", 30], ["90d", 90], ["1y", 365], ["All", 100000]];
var winDays = 365, useLog = false;

function dateSpan() {
  var first = null, last = null;
  SERIES.forEach(function (s) {
    if (s.dates && s.dates.length) {
      if (!first || s.dates[0] < first) first = s.dates[0];
      var d = s.dates[s.dates.length - 1];
      if (!last || d > last) last = d;
    }
  });
  return [
    first ? Date.parse(first + "T00:00:00Z") : Date.now(),
    last ? Date.parse(last + "T00:00:00Z") : Date.now()
  ];
}

function draw() {
  var span = dateSpan();
  var t1 = span[1];
  var t0 = Math.max(t1 - winDays * 86400000, span[0]);
  var lines = [], vmin = Infinity, vmax = -Infinity;
  SERIES.forEach(function (s) {
    var pts = [];
    if (s.flat) {
      pts = [[t0, 100], [t1, 100]];
    } else {
      var base = null;
      for (var k = 0; k < s.dates.length; k++) {
        var t = Date.parse(s.dates[k] + "T00:00:00Z");
        if (t < t0 || t > t1) continue;
        var v = s.values[k];
        if (v === null || v === undefined || v <= 0) continue;
        if (base === null) base = v;
        pts.push([t, v / base * 100]);
      }
    }
    pts.forEach(function (p) {
      var y = useLog ? Math.log(p[1]) : p[1];
      if (y < vmin) vmin = y;
      if (y > vmax) vmax = y;
    });
    if (pts.length > 1) lines.push({s: s, pts: pts});
  });
  if (!lines.length) {
    document.getElementById("chart").innerHTML =
      "<p style='color:#8a8f9c'>No series to draw yet.</p>";
    return;
  }
  if (vmax - vmin < 1e-9) { vmax += 1; vmin -= 1; }
  var W = 920, H = 320, L = 46, R = 8, T = 10, B = 24;
  function X(t) { return L + (t - t0) / (t1 - t0) * (W - L - R); }
  function Y(v) {
    var y = useLog ? Math.log(v) : v;
    return T + (vmax - y) / (vmax - vmin) * (H - T - B);
  }
  var svg = ['<svg viewBox="0 0 ' + W + " " + H + '" style="width:100%">'];
  var steps = 4;
  for (var g = 0; g <= steps; g++) {
    var y = vmin + (vmax - vmin) * g / steps;
    var v = useLog ? Math.exp(y) : y;
    var py = T + (vmax - y) / (vmax - vmin) * (H - T - B);
    svg.push('<line class="grid" x1="' + L + '" y1="' + py + '" x2="' + (W - R) +
      '" y2="' + py + '"/>');
    svg.push('<text x="' + (L - 6) + '" y="' + (py + 4) +
      '" text-anchor="end">' + v.toFixed(0) + "</text>");
  }
  for (var xt = 0; xt <= 4; xt++) {
    var t = t0 + (t1 - t0) * xt / 4;
    var anchor = xt === 0 ? "start" : xt === 4 ? "end" : "middle";
    svg.push('<text x="' + X(t) + '" y="' + (H - 6) +
      '" text-anchor="' + anchor + '">' +
      new Date(t).toISOString().slice(0, 10) + "</text>");
  }
  lines.forEach(function (ln) {
    var d = ln.pts.map(function (p, i) {
      return (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1);
    }).join("");
    svg.push('<path d="' + d + '" fill="none" stroke="' + ln.s.color +
      '" stroke-width="1.6"' + (ln.s.dash ? ' stroke-dasharray="' + ln.s.dash + '"' : "") +
      "/>");
  });
  svg.push("</svg>");
  document.getElementById("chart").innerHTML = svg.join("");
  document.getElementById("legend").innerHTML = lines.map(function (ln) {
    return "<span><i style='background:" + ln.s.color + "'></i>" + ln.s.name + "</span>";
  }).join("");
}

var winBox = document.getElementById("win");
windows.forEach(function (w) {
  var b = document.createElement("button");
  b.textContent = w[0];
  if (w[1] === winDays) b.className = "active";
  b.onclick = function () {
    winDays = w[1];
    useLog = w[1] > 400;
    logBox.checked = useLog;
    winBox.querySelectorAll("button").forEach(function (x) { x.className = ""; });
    b.className = "active";
    draw();
  };
  winBox.appendChild(b);
});
var logLabel = document.createElement("label");
var logBox = document.createElement("input");
logBox.type = "checkbox";
logBox.onchange = function () { useLog = logBox.checked; draw(); };
logLabel.appendChild(logBox);
logLabel.appendChild(document.createTextNode(" log scale"));
winBox.appendChild(logLabel);
draw();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
