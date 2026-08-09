"""Build the benchmarks page into site/invest.html.

Usage:
    python -m report.invest             # reads the invest/ artifacts from S3
    python -m report.invest --local     # renders the empty-state page, no AWS

Reads the weekly index artifact (invest/bench.json.gz), the daily NAV series
(invest/nav.json.gz), the daily craft-carry series (invest/craft.json.gz)
and the daily luxury desk artifact (invest/luxury/latest.json.gz), and
renders the "are we ahead of the market" page: benchmark lines (index, raw
gold, ecto, gem rate), the NAV line, the allocation caps, the holdings class
breakdown with its structural floor coverage, time-gated craft carry,
today's luxury desk picks and the corner screen (docs/INVESTING.md
strategies S8, S4, S7 and S6).

Two words that used to mean two different things on one screen, now
separated: the NAV valuation convention is the "liquidation mark" (its JSON
keys stay floor/stock_floor, which the stored series depends on), and
"structural floor" is S7's bound. "Exit floor" on site/index.html is a third
thing again (a sizing constraint) and is untouched.

Privacy rule: the NAV series is stored in the private bucket in absolute
gold, but everything published here is rebased to 100 or expressed as a
percentage, so the public page never carries absolute wealth. The bankroll
(BANKROLL_G) is used only as a denominator for the same reason. The craft
carry and luxury desk tables are an exception by design, the same way the
flip report shows prices: cost/sell/capital there, and the corner screen's
swallow cost, average cost, relist and ceiling, are market data and a
recommendation, not the user's own holdings, so showing them in gold is not
a wealth leak. Floor coverage is the opposite case and is published as a
unitless ratio only: this page already prints bankroll_share per class, so a
single per-class floor in gold would give class_value = floor_gold /
floor_coverage and from there BANKROLL_G = class_value / bankroll_share.
"""

import json
import os
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from account.pnl import gold
from invest import bankroll, tags
from invest.corner import CORNER_KEY
from invest.craft import CRAFT_KEY
from invest.index import BENCH_KEY, trailing_return
from invest.luxury import LUXURY_KEY
from invest.nav import NAV_KEY
from invest.radar import RADAR_KEY, RADAR_MIN_OBS, RADAR_Z_THRESHOLD
from invest.release_study import RELEASES_KEY

SITE = Path(__file__).resolve().parent.parent / "site"

WINDOWS = (("30d", 30), ("90d", 90), ("365d", 365), ("all", 36500))
CRAFT_EVIDENCE_DAYS = 30  # trailing window for the "live X of Y days" tally

CLASS_LABELS = {
    "t6": "T6 fine mats",
    "festival": "festival",
    "staples": "staples",
    "luxury": "luxury (500g+)",
    "other": "everything else",
}

CLASS_STUDY_LABELS = {"t6": "T6 fine mats", "staples": "staples"}


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
    # The ratio is publishable, the denominator is not: site/invest.html
    # already prints bankroll_share per class, so one gold figure in this
    # payload would give class_value = floor_gold / floor_coverage and then
    # BANKROLL_G = class_value / bankroll_share. Copper stops here.
    structural = latest.get("stock_floor_structural")
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
        "floor_coverage": round(structural / stock, 4)
        if structural is not None and stock
        else None,
        "floor_items": latest.get("floor_items"),
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
    floor_classes = latest.get("floor_classes") or {}
    stock = latest.get("stock_floor") or 0
    rows = []
    for cls in tags.CLASSES:
        v = classes.get(cls, 0)
        f = floor_classes.get(cls)
        rows.append(
            {
                "cls": cls,
                "label": CLASS_LABELS.get(cls, cls),
                "stock_share": round(v / stock, 4) if stock else None,
                "bankroll_share": round(v / bankroll_copper, 4)
                if bankroll_copper
                else None,
                # A ratio and never a level, for the reason nav_public
                # states: two ratios over one class carry no scale, one
                # gold figure would unmask the bankroll.
                "floor_coverage": round(f / v, 4) if f is not None and v else None,
            }
        )
    return rows


def craft_rows(series, window=CRAFT_EVIDENCE_DAYS):
    """Latest reading per refinement plus a trailing live/dead tally.

    live_days counts points in the trailing `window` where the chain priced
    a spread at or above the tax hurdle; tracked_days counts points where it
    priced at all (Charged Quartz Crystal never does, by design, so both
    stay 0 for it). An empty series returns [].
    """
    points = (series or {}).get("points") or []
    if not points:
        return []
    recent = points[-window:]
    rows = []
    for c in points[-1]["chains"]:
        history = [
            next((x for x in p["chains"] if x["key"] == c["key"]), None)
            for p in recent
        ]
        priced = [h for h in history if h and h.get("dead") is not None]
        rows.append(
            {
                **c,
                "tracked_days": len(priced),
                "live_days": sum(1 for h in priced if not h["dead"]),
            }
        )
    return rows


def luxury_public(doc):
    """The publishable slice of the luxury desk artifact, or None without one."""
    if not doc or not doc.get("picks"):
        return None
    return {
        "generated": doc.get("generated"),
        "through": doc.get("through"),
        "universe_n": doc.get("universe_n"),
        "picks": doc.get("picks"),
    }


def release_public(doc):
    """The publishable slice of the release-study artifact, or None without
    one yet - returns/hit-rate/confidence are all ratios about the market,
    not the user's holdings, so this needs no privacy rebasing."""
    if not doc or not doc.get("classes"):
        return None
    return {
        "generated": doc.get("generated"),
        "through": doc.get("through"),
        "classes": doc["classes"],
    }


def build_payload(bench, nav, bankroll_copper, craft_series=None, luxury_doc=None,
                   theses=None, releases_doc=None, radar_doc=None, corner_doc=None):
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
        "craft": craft_rows(craft_series),
        "luxury": luxury_public(luxury_doc),
        "theses": theses or [],
        "releases": release_public(releases_doc),
        "radar": radar_doc,
        "corner": corner_doc,
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


def coverage(v):
    """Floor coverage as a cell: "n/a" when nothing was computable, "at
    floor" once the structural floor reaches the liquidation mark, a
    percentage otherwise. Never a negative percentage, and never 0% for a
    class whose floor was simply never measurable (luxury genuinely has
    none), which is what separates "unmeasured" from "measured and bad"."""
    if v is None:
        return "n/a"
    return "at floor" if v >= 1 else share(v)


def exposure_table(payload):
    rows = payload["exposure"]
    if not rows:
        return (
            "<p>No NAV series yet. Add a GW2_API_KEY repository secret (scopes: "
            "account, wallet, inventories, characters, tradingpost) and the "
            "daily report starts appending one point per day.</p>"
        )
    cells = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            escape(r["label"]), share(r["stock_share"]), share(r["bankroll_share"]),
            coverage(r.get("floor_coverage")),
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
            meta.append(f"patient mark {pct(nav['mark_premium'])} above the liquidation mark")
        if nav.get("floor_coverage") is not None:
            items = nav.get("floor_items")
            tail = f" across {items:,} items" if items else ""
            meta.append(
                f"structural floors underwrite {coverage(nav['floor_coverage'])} "
                f"of stock{tail}"
            )
    return (
        "<table><thead><tr><th>Class</th><th>Share of stock</th>"
        "<th>Share of bankroll</th><th>Floor coverage</th></tr></thead>"
        f"<tbody>{cells}</tbody></table>"
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


def craft_status(r):
    """(label, css class) for one craft-carry row's status cell."""
    if r["dead"] is None:
        return ("no tradable output", "na") if r["output_id"] is None else ("unpriced", "na")
    return ("dead", "dead") if r["dead"] else ("live", "live")


def craft_table(payload):
    rows = payload.get("craft") or []
    if not rows:
        return (
            "<p>No craft-carry artifact yet; the daily invest.craft step "
            "fills this in.</p>"
        )
    cells = []
    for r in rows:
        label, cls = craft_status(r)
        live = f"{r['live_days']}/{r['tracked_days']}" if r["tracked_days"] else ""
        name = escape(r["name"])
        if r["output_id"]:
            name = f'<a href="https://www.gw2bltc.com/en/item/{r["output_id"]}">{name}</a>'
        cells.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
            '<td class="carry-{}">{}</td><td>{}</td></tr>'.format(
                name,
                gold(r["cost"]) if r["cost"] is not None else "",
                gold(r["revenue"]) if r["revenue"] is not None else "",
                pct(r["spread"], 1) if r["spread"] is not None else "",
                cls, label, live,
            )
        )
    return (
        "<table><thead><tr><th>Refinement</th><th>Cost</th><th>Sell</th>"
        f"<th>Spread</th><th>Status</th><th>Live days ({CRAFT_EVIDENCE_DAYS}d)"
        f"</th></tr></thead><tbody>{''.join(cells)}</tbody></table>"
    )


def luxury_table(payload):
    lux = payload.get("luxury")
    if not lux:
        return (
            "<p>No luxury desk artifact yet; the daily invest.luxury step "
            "fills this in once it has collected an order book.</p>"
        )
    rows = lux.get("picks") or []
    cells = "".join(
        "<tr><td><a href=\"https://www.gw2bltc.com/en/item/{}\">{}</a></td>"
        "<td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
        "<td>{}</td><td>{}</td></tr>".format(
            p["id"], escape(p["name"]), gold(p["buy_at"]), gold(p["sell_at"]),
            share(p["margin_pct"]), p["qty"], gold(p["capital"]),
            f"{p['entry_flow_week']:g}/wk", f"{p['exit_flow_week']:g}/wk",
        )
        for p in rows
    )
    meta = (
        f"{lux.get('universe_n', 0):,} items in the tracked universe, "
        f"{len(rows)} priced with a placeable margin, as of {lux.get('through', '?')}"
    )
    return (
        f"<p>{meta}</p>"
        "<table><thead><tr><th>Item</th><th>Bid at</th><th>Ask at</th>"
        "<th>Margin</th><th>Qty</th><th>Capital</th><th>Entry flow</th>"
        f"<th>Exit flow</th></tr></thead><tbody>{cells}</tbody></table>"
    )


def thesis_table(payload):
    """One row per registry entry regardless of bucket - a small,
    hand-curated registry is not the auto-generated season candidate list
    that needed opens_soon/dormant suppression, so watching entries render
    too. "avoid" rows get a distinct badge and no qty/capital columns:
    nothing was ever sized or tracked for them."""
    rows = payload.get("theses") or []
    if not rows:
        return "<p>No thesis registry entries yet; add one to invest/theses.yaml.</p>"
    cells = []
    for r in rows:
        name = escape(r.get("name") or "")
        bucket = escape(r.get("bucket") or "")
        dtc = r.get("days_to_catalyst")
        dti = r.get("days_to_invalidation")
        if r.get("direction") == "avoid":
            cells.append(
                '<tr><td>{}</td><td class="thesis-avoid">avoid</td><td>{}</td>'
                "<td></td><td></td><td>{}</td><td>{}</td></tr>".format(
                    name, bucket, dtc, dti
                )
            )
            continue
        cells.append(
            "<tr><td>{}</td><td>long</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td></tr>".format(
                name, bucket, pct(r.get("dist_to_ceiling")),
                pct(r.get("dist_to_target")), dtc, dti,
            )
        )
    return (
        "<table><thead><tr><th>Item</th><th>Direction</th><th>Bucket</th>"
        "<th>vs ceiling</th><th>vs target</th><th>Days to catalyst</th>"
        f"<th>Days to invalidation</th></tr></thead><tbody>{''.join(cells)}</tbody></table>"
    )


def release_table(payload):
    rel = payload.get("releases")
    if not rel or not rel.get("classes"):
        return (
            "<p>No release event-study artifact yet; the weekly invest "
            "workflow fills this in.</p>"
        )
    rows = []
    for c in rel["classes"]:
        cov = c.get("coverage") or {}
        rows.append(
            "<tr><td>{}</td><td>{} of {}</td><td>{}</td><td>{}</td></tr>".format(
                escape(CLASS_STUDY_LABELS.get(c["class"], c["class"])),
                cov.get("launches_priced", 0), cov.get("launches_total", 0),
                share(c.get("hit_rate"), 0) or "n/a", escape(c.get("confidence") or ""),
            )
        )
    body = "".join(rows)
    note = (
        "<p>Festival, luxury and other are intentionally excluded from this "
        "study: festival is already covered by the seasonal-carry evidence "
        "under a different lens and would double-count here, luxury is a "
        "live-price threshold rule with no historically-valid retroactive "
        "equivalent, and other has no coherent thesis to aggregate. Six "
        "launches (HoT through VoE) is a hard sample ceiling; both class "
        "rows always render regardless of n, and every launch count is "
        "shown raw rather than as a bare percentage.</p>"
    )
    return (
        "<table><thead><tr><th>Class</th><th>Launches priced</th>"
        f"<th>Hit rate</th><th>Confidence</th></tr></thead><tbody>{body}</tbody></table>{note}"
    )


def radar_table(payload):
    radar = payload.get("radar")
    if not radar:
        return (
            "<p>No market radar artifact yet; the daily invest.radar step "
            "fills this in.</p>"
        )
    history_days = radar.get("history_days") or 0
    # invest.radar.radar_rows drops today's point into the baseline (see
    # `*baseline, latest = points`), so the baseline is history_days - 1
    # points; it needs RADAR_MIN_OBS of those before a z-score is trusted,
    # i.e. history_days >= RADAR_MIN_OBS + 1.
    radar_min_days = RADAR_MIN_OBS + 1
    if history_days < radar_min_days:
        return (
            f"<p>Still collecting the rolling baseline ({history_days} of "
            f"{radar_min_days} minimum days); the abnormal-activity and pump "
            "screens fill in once enough history has accumulated. The sink "
            "watchlist below needs no history and is live already.</p>"
        )
    abnormal = radar.get("abnormal") or []
    pumps = radar.get("pumps") or []
    sinks = radar.get("sinks") or []

    abnormal_body = "".join(
        '<tr><td><a href="https://www.gw2bltc.com/en/item/{0}">{0}</a></td><td>{1}</td></tr>'.format(
            r["id"],
            ", ".join(
                f"{k} {v:+.1f}" for k, v in (r.get("z") or {}).items() if v is not None
            ),
        )
        for r in abnormal[:40]
    )
    abnormal_html = (
        f"<h3>Abnormal activity ({len(abnormal)} flagged)</h3>"
        "<table><thead><tr><th>Item</th><th>|z| >= "
        f"{RADAR_Z_THRESHOLD:g} on</th></tr></thead>"
        f"<tbody>{abnormal_body}</tbody></table>"
        if abnormal
        else f"<h3>Abnormal activity</h3><p>Nothing flagged today.</p>"
    )

    pump_html = (
        f"<h3>Pump filter ({len(pumps)} flagged)</h3>"
        "<p>" + ", ".join(
            f'<a href="https://www.gw2bltc.com/en/item/{i}">{i}</a>' for i in pumps
        ) + "</p>"
        if pumps
        else "<h3>Pump filter</h3><p>Nothing flagged today.</p>"
    )

    sink_body = "".join(
        '<tr><td><a href="https://www.gw2bltc.com/en/item/{}">{}</a></td>'
        "<td>{}</td><td>{}</td></tr>".format(
            r["id"], escape(r["name"]), gold(r["sell_price"]), r["flow_7d_avg"]
        )
        for r in sinks[:40]
    )
    sink_html = (
        f"<h3>Sink-anticipation watchlist ({len(sinks)})</h3>"
        "<table><thead><tr><th>Item</th><th>Sell price</th>"
        f"<th>7d avg sell flow</th></tr></thead><tbody>{sink_body}</tbody></table>"
        if sinks
        else "<h3>Sink-anticipation watchlist</h3><p>Nothing pinned at the "
        "vendor floor with enough flow today.</p>"
    )
    return abnormal_html + pump_html + sink_html


def corner_table(payload):
    doc = payload.get("corner")
    if not doc:
        return (
            "<p>No corner screen artifact yet; the daily invest.corner step "
            "fills this in.</p>"
        )
    picks = doc.get("picks") or []
    rejected = doc.get("rejected") or []
    meta = (
        f"{doc.get('candidates', 0):,} rows survived the free prefilter and had "
        f"their real ladder walked against a ceiling, as of "
        f"{doc.get('through', '?')}; {len(picks)} are actionable."
    )
    if picks:
        cells = "".join(
            "<tr><td><a href=\"https://www.gw2bltc.com/en/item/{}\">{}</a></td>"
            "<td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                p["id"], escape(p["name"]), gold(p["cost"]), gold(p["avg_cost"]),
                gold(p["relist_at"]), gold(p["ceiling"]), escape(p["ceiling_source"]),
                f"{p['mult']:.2f}x", f"{p['demand_per_day']:g}",
                f"{p['refill_per_day']:g}" + ("*" if p.get("refill_unobserved") else ""),
                f"{p['race']:.2f}", f"{p['score']:.2f}",
                f"{p['unload_days']:.0f}d", pct(p.get("bail_out_pct")),
            )
            for p in picks
        )
        table = (
            "<table><thead><tr><th>Item</th><th>Swallow cost</th><th>Avg cost</th>"
            "<th>Relist</th><th>Ceiling</th><th>Ceiling from</th><th>Multiple</th>"
            "<th>Demand/day</th><th>Refill/day</th><th>Race</th><th>Score</th>"
            f"<th>Unload</th><th>Bail-out</th></tr></thead><tbody>{cells}</tbody></table>"
            "<p>Check the swallow cost against your own per-corner cap before "
            "any gold moves; this table deliberately carries no bankroll-derived "
            "figure. A refill rate marked * was never observed, so it sits at the "
            "floor the window could measure rather than at zero.</p>"
        )
    else:
        table = (
            "<p>Nothing clears the screen today. An empty table is the normal "
            "output: at the strict ceiling almost everything fails on the "
            "unload horizon or on ceiling headroom, and the near-miss list "
            "below is what makes that legible.</p>"
        )
    if rejected:
        items = "".join(
            '<li><a href="https://www.gw2bltc.com/en/item/{}">{}</a>: {}</li>'.format(
                r["id"], escape(r["name"]), escape(r["reason"])
            )
            for r in rejected[:40]
        )
        table += (
            f"<h3>Screened, not actionable ({len(rejected)})</h3>"
            f"<ul class=\"rejects\">{items}</ul>"
        )
    return f"<p>{escape(meta)}</p>{table}"


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
    html = html.replace("__CRAFT__", craft_table(payload))
    html = html.replace("__LUXURY__", luxury_table(payload))
    html = html.replace("__THESIS__", thesis_table(payload))
    html = html.replace("__RELEASES__", release_table(payload))
    html = html.replace("__RADAR__", radar_table(payload))
    html = html.replace("__CORNERS__", corner_table(payload))
    # </ would end the script block early if an item name ever carried it
    html = html.replace("__DATA__", json.dumps(payload).replace("</", "<\\/"))
    return html


def _read_actions_theses():
    """site/actions.json's "theses" key: report.build already ran earlier
    in the same job, so this is a local read, not a second S3 round trip."""
    try:
        doc = json.loads((SITE / "actions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return doc.get("theses") or []


def main():
    local = "--local" in sys.argv
    bench = nav = craft_series = luxury_doc = releases_doc = radar_doc = None
    corner_doc = None
    if not local:
        from collector.s3store import Store

        store = Store(os.environ["S3_BUCKET"])
        bench = store.get_json_gz(BENCH_KEY)
        nav = store.get_json_gz(NAV_KEY)
        craft_series = store.get_json_gz(CRAFT_KEY)
        luxury_doc = store.get_json_gz(LUXURY_KEY)
        releases_doc = store.get_json_gz(RELEASES_KEY)
        radar_doc = store.get_json_gz(RADAR_KEY)
        corner_doc = store.get_json_gz(CORNER_KEY)

    theses = _read_actions_theses()
    payload = build_payload(
        bench, nav, bankroll.bankroll_copper(), craft_series, luxury_doc,
        theses, releases_doc, radar_doc, corner_doc,
    )
    SITE.mkdir(exist_ok=True)
    (SITE / "invest.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    (SITE / "invest.html").write_text(render(payload), encoding="utf-8")
    print(
        f"benchmarks page (index: {'yes' if bench else 'no'}, "
        f"nav points: {len((nav or {}).get('points') or [])}, "
        f"craft points: {len((craft_series or {}).get('points') or [])}, "
        f"luxury picks: {len((luxury_doc or {}).get('picks') or [])}, "
        f"theses: {len(theses)}, "
        f"releases: {'yes' if releases_doc else 'no'}, "
        f"radar: {'yes' if radar_doc else 'no'}, "
        f"corner picks: {len((corner_doc or {}).get('picks') or [])}) "
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
.carry-live { color: #7ec97f; } .carry-dead { color: #8a8f9c; } .carry-na { color: #8a8f9c; }
.notes { color: #8a8f9c; font-size: 13px; margin-top: 24px; }
.notes ul { margin: 6px 0; padding-left: 20px; }
ul.rejects { color: #8a8f9c; font-size: 13px; margin: 6px 0; padding-left: 20px; }
ul.rejects a { color: #aab0bd; }
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
<h2>Time-gated craft carry</h2>
<p>Four of the five daily-cooldown Mystic Forge materials are account-bound
themselves but refine, uncapped, straight into a tradable tier-7 material;
the fifth, Charged Quartz Crystal, has no single tradable next step and is
tracked for its input cost only. A carry is marked dead once its spread
drops under the 15% tax itself, not just below zero.</p>
__CRAFT__
<h2>Luxury desk</h2>
<p>Strategy S4: the 500g+ tier trades at wide spreads on 1-5 sales a week,
too thin for the flip crowd to size. Bid at, sell at are today's patient
placements, gap-walked into the live book the same way the flip table's
prices are; qty is capped by this item's own exit flow over a 30-day
horizon and by a few units per item so the book stays spread across names.
Picks open as paper positions on <a href="track.html">the track record
page</a> under the luxury desk strategy tag.</p>
__LUXURY__
<h2>Thesis registry</h2>
<p>Strategy S2: a small, hand-edited registry of item-leg convictions
(<a href="https://github.com/PetrCala/gw2-advisor/blob/main/invest/theses.yaml">invest/theses.yaml</a>),
each with a catalyst, a date, an entry ceiling, a target and an
invalidation rule. Every entry renders here regardless of bucket so a
missed nag is never the only way to see current state; "avoid" entries are
notify-only and are never sized or paper-tracked.</p>
__THESIS__
<h2>Release event study</h2>
<p>Strategy S1: per-launch tax-adjusted returns for the material classes
with a curated id set matching the strategy's own thesis text.</p>
__RELEASES__
<h2>Market radar</h2>
<p>Strategy S2's cheap screens over the daily snapshot: an abnormal-activity
z-score radar against a self-collected rolling baseline, a pump filter, and
a sink-anticipation watchlist. The z-score screens have a real burn-in
period (about 90 days) before the baseline is trustworthy; the sink
watchlist needs no history and is live from day one.</p>
__RADAR__
<h2>Corners and buyouts</h2>
<p>Strategy S6: buy a thin sell book out to a price and relist above it. The
score is the smaller of two ratios, both breaking even at 1.00: the race
(how much faster our own capture share of demand unloads the lot than new
listings refill the book) and the ceiling multiple (the relist net of the
15% cut over what the lot cost). The relist can never exceed the cheapest
alternative route to the item, which is a recipe or vendor conversion where
one exists and the 12-month traded high otherwise, so a plan is never built
on the fantasy asks resting at the top of a thin ladder.</p>
<p><strong>The largest risk here is one this model cannot measure at all:
every plan assumes today's demand survives at the raised price. It will not,
and nothing in the data says by how much.</strong> Nor can the data separate
end demand from other flippers buying to relist, so cornering into flipper
demand is cornering into your own future competition. Treat a passing score
as a candidate for a human to underwrite, never as a decision.</p>
__CORNERS__
__CONSTITUENTS__
<div class="notes">
<ul>
<li>Index: constituents are the top 500 items by prior-month sell-side
turnover, reweighted monthly, single item capped at 5%; levels chain daily
from item mid prices, renormalized over what actually traded. It starts
mid-2019 because the mirror carries no fills before then.</li>
<li>NAV marks every holding at its liquidation value (a dump into the
current best bid, net of the 15% cut); the sell-listing mark is shown only
as a premium. Absolute gold never appears here: the NAV line is rebased to
100 and shares are percentages.</li>
<li>Floor coverage is the share of a class's liquidation mark underwritten
by a structural floor (strategy S7): the vendor buy-back price, or the most
any conversion that consumes the item could afford to pay for it as fodder,
whichever is higher. It is reported, never a filter: risk rule 7 says
"prefer positions with computable floors", which is a preference, so nothing
here gates a pick or shrinks a lot. "n/a" means no floor was computable for
that class at all, which is different from a floor of zero. The series
cannot be backfilled (no record of past holdings or past vendor values
exists), so it starts from today.</li>
<li>The corner ceiling is the cheapest alternative route to one unit:
ingredients at the queue front, account-bound catalysts costed at zero and a
random-output recipe taken at its maximum yield, so the ceiling comes out as
low as it defensibly can. Ceiling from tells you whether that moat is
structural (forge, merchant, double-click) or only historical (traded high),
which matters because ceilings move: the 2016-17 hardened leather squeeze
ended when ArenaNet shipped a leather farm. Bail-out is an estimate of
dumping the whole lot into today's buy book, shown because a thin buy book
is what makes a corner possible in the first place; it is not a floor and
another trader's wall is not your exit liquidity.</li>
<li>The gem line is the cost of buying 100 gems with gold (gw2tp history
plus the official exchange spot). It is the closest thing to an outside
inflation gauge, not a tradable series: the round trip loses ~35%.</li>
<li>Craft carry cost and sell are one order-width inside the book (buy 1
above the best bid, sell 1 below the best ask), the same queue-front
convention as the daily flip table, revenue net of the 15% cut. Live days
counts trailing days the spread cleared the tax hurdle, out of days it
priced at all.</li>
<li>Luxury desk universe: items priced 500g or more with at least one sale
in the trailing week, minus a small curated exclusion list (generation-1
precursors, policy-broken since 2023). Entry/exit flow blends the 7-day
and 1-month rolling fill counts so one heavy or dead day cannot swing an
estimate this thin; margin is net of the 15% cut and only clears the desk
at 10% or better. An order that sits unfilled past three weeks is
repriced or withdrawn by rule, not judgment; the paper track record flags
this as a stale position rather than a missed sale.</li>
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
