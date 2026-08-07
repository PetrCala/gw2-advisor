"""Post seasonal action-queue transitions and keep the RSS feed.

Runs as a report-workflow step right after report.build, in the same job,
so it sees the fresh site/actions.json and still holds AWS credentials.
It diffs the queue against the previous run's snapshot in S3, appends any
transitions to a rolling event log, rewrites site/feed.xml from that log,
and posts the transitions as one comment on the rolling trade-actions
issue (gh CLI with the workflow token). The first run is a quiet
baseline: state gets saved, nothing is posted, so enabling this never
spams the backlog as if every pick just transitioned.

Usage:
    python -m report.notify    # needs S3_BUCKET; comments only when
                               # TRADE_ACTIONS_ISSUE and gh are available
"""

import json
import os
import subprocess
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
PREV_KEY = "notify/actions-prev.json.gz"
LOG_KEY = "notify/log.json.gz"
LOG_CAP = 100  # feed depth; older transitions live on in the issue thread
REPORT_URL = "https://petrcala.github.io/gw2-advisor/"


def fmt_money(c):
    c = int(round(c))
    g, s, k = c // 10000, c % 10000 // 100, c % 100
    parts = []
    if g:
        parts.append(f"{g}g")
    if g or s:
        parts.append(f"{s}s")
    parts.append(f"{k}c")
    return " ".join(parts)


def _ev(kind, row, line):
    return {"kind": kind, "id": row["id"], "name": row["name"], "line": line}


def diff_queues(prev, cur):
    """Transition events between two queue snapshots, in queue order.

    Only edges someone would act on: a pick becoming a buy, opening soon,
    hitting its sell window, or ceasing to be a buy. Staying in a bucket
    is silence.
    """
    was = {r["id"]: r for r in prev}
    now = {r["id"]: r for r in cur}
    events = []
    for r in cur:
        old = was.get(r["id"])
        ob = old["bucket"] if old else None
        b = r["bucket"]
        if b == "buy_now" and ob != "buy_now":
            qty = f", qty {r['suggested_qty']:,}" if r.get("suggested_qty") else ""
            events.append(_ev(
                "buy", r,
                f"BUY {r['name']}: pay up to {fmt_money(r['limit_price'])} "
                f"(now {fmt_money(r['cur_price'])}){qty}, "
                f"closes {r['buy_closes']}",
            ))
        elif b == "sell_active" and ob != "sell_active":
            events.append(_ev(
                "sell", r,
                f"SELL {r['name']}: sell window closes {r['sell_closes']}",
            ))
        elif b == "opens_soon" and ob not in ("opens_soon", "buy_now"):
            pay = (
                f", pay up to {fmt_money(r['limit_price'])}"
                if r.get("limit_price") else ""
            )
            events.append(_ev(
                "soon", r,
                f"SOON {r['name']}: buy window opens {r['buy_opens']}{pay}",
            ))
        if ob == "buy_now" and b != "buy_now":
            events.append(_ev(
                "over", r, f"OVER {r['name']}: no longer a buy ({r['verdict']})"
            ))
    for r in prev:
        if r["bucket"] == "buy_now" and r["id"] not in now:
            events.append(_ev(
                "over", r, f"OVER {r['name']}: dropped from the picks"
            ))
    return events


def render_comment(events, generated):
    lines = "\n".join(f"- {e['line']}" for e in events)
    return f"{lines}\n\n[report]({REPORT_URL}) as of {generated}"


def _rfc822(iso):
    d = date.fromisoformat(iso)
    return d.strftime("%a, %d %b %Y") + " 05:00:00 GMT"


def render_feed(log):
    """RSS 2.0 over the event log, newest first."""
    items = []
    for e in reversed(log[-LOG_CAP:]):
        items.append(
            "<item>"
            f"<title>{escape(e['line'])}</title>"
            f"<link>{REPORT_URL}</link>"
            f'<guid isPermaLink="false">{e["date"]}-{e["id"]}-{e["kind"]}</guid>'
            f"<pubDate>{_rfc822(e['date'])}</pubDate>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        "<title>gw2-advisor trade actions</title>"
        f"<link>{REPORT_URL}</link>"
        "<description>Seasonal action-queue transitions</description>"
        + "".join(items)
        + "</channel></rss>\n"
    )


def main():
    actions_path = SITE / "actions.json"
    if not actions_path.exists():
        print("no site/actions.json; nothing to diff")
        return
    cur = json.loads(actions_path.read_text(encoding="utf-8"))

    from collector.s3store import Store

    store = Store(os.environ["S3_BUCKET"])
    prev = store.get_json_gz(PREV_KEY)
    log = store.get_json_gz(LOG_KEY) or []

    events = []
    if prev is None:
        print(f"baseline: {len(cur['queue'])} queue rows saved, nothing posted")
    else:
        events = diff_queues(prev["queue"], cur["queue"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for e in events:
        e["date"] = today
    log = (log + events)[-LOG_CAP:]

    (SITE / "feed.xml").write_text(render_feed(log), encoding="utf-8")
    store.put_json_gz(PREV_KEY, cur)
    store.put_json_gz(LOG_KEY, log)

    if not events:
        print("no transitions")
        return
    for e in events:
        print(e["line"])
    issue = os.environ.get("TRADE_ACTIONS_ISSUE")
    if not issue:
        print("TRADE_ACTIONS_ISSUE unset; not commenting")
        return
    subprocess.run(
        ["gh", "issue", "comment", issue,
         "--body", render_comment(events, cur["generated"])],
        check=True,
    )
    print(f"{len(events)} transitions -> issue #{issue}")


if __name__ == "__main__":
    main()
