"""Weekly release event study (M6.d, docs/INVESTING.md strategy S1): per-
launch returns for the two material classes with a curated id set matching
S1's own thesis text - T6 and STAPLES. FESTIVAL is already covered by S3
under a different lens and would double-count here; LUXURY is a live-price
threshold rule with no historically-correct retroactive equivalent; "other"
has no coherent thesis to aggregate.

Six launches (HoT through VoE) is a hard sample ceiling, so every row
always prints its raw n ("4 of 6 launches priced") rather than a bare
percentage, and both class rows always render regardless of n - unlike
season/score.py's seasonal ranking, there is no MIN_CYCLES gate here, since
with only two rows total, blanket suppression would make the section look
broken rather than honest about a thin sample.

Cheap: 17 item ids (T6 + STAPLES) against 6 fixed launch dates, one pass
over history/dw2/. Runs weekly in invest.yml as a second step, reusing that
job's already-open AWS OIDC session; no new cron.

Usage:
    python -m invest.release_study    # needs S3_BUCKET
"""

import io
from statistics import median

from invest import tags
from season import cycles, events, score

HISTORY_PREFIX = "history/dw2/"
RELEASES_KEY = "invest/releases.json.gz"

CLASS_IDS = {"t6": sorted(tags.T6), "staples": sorted(tags.STAPLES)}


def load_series(store, item_ids):
    """{item_id: {"YYYY-MM-DD": price}} daily price series (season.cycles.
    daily_price's pre-2019 avg->min/max-midpoint fallback) for exactly
    `item_ids`, streamed once over every history/dw2/ chunk - the same
    single-pass-over-everything shape invest.index.load uses, just filtered
    down to this study's 17 ids instead of kept dense.
    """
    import pyarrow.parquet as pq

    wanted = set(item_ids)
    series = {i: {} for i in item_ids}
    for key in store.list_keys(HISTORY_PREFIX):
        data = store.get_bytes(key)
        if data is None:
            continue
        t = pq.read_table(
            io.BytesIO(data),
            columns=["date", "item_id", "sell_price_avg", "sell_price_min", "sell_price_max"],
        )
        rows = zip(
            t["date"].to_pylist(), t["item_id"].to_pylist(),
            t["sell_price_avg"].to_pylist(), t["sell_price_min"].to_pylist(),
            t["sell_price_max"].to_pylist(),
        )
        for ts, item_id, avg, lo, hi in rows:
            if item_id not in wanted:
                continue
            p = cycles.daily_price(avg, lo, hi)
            if p and p > 0:
                series[item_id][ts.date().isoformat()] = p
    return series


def class_cycles(item_ids, series_by_id, windows, through=None):
    """Per-launch cross-sectional median return across `item_ids`' own
    priced legs, in `windows`' order, plus an {items_priced, items_total}
    coverage count per launch - an id missing a window is dropped by
    season.cycles.window_price (via release_returns), not guessed, so a
    shrinking basket stays visible instead of silently averaging under an
    unchanging label.
    """
    out = []
    for label, win in windows.items():
        _, _, _, s1 = win
        if through and s1 > through:
            continue
        rets = []
        for item_id in item_ids:
            r = cycles.release_returns(series_by_id.get(item_id) or {}, {label: win})
            if r:
                rets.append(r[0]["ret"])
        if not rets:
            continue
        out.append(
            {
                "label": label,
                "ret": round(median(rets), 3),
                "items_priced": len(rets),
                "items_total": len(item_ids),
            }
        )
    return out


def build_classes(store, today):
    """[{class, cycles, hit_rate, confidence, coverage}] for t6 and
    staples, both always emitted regardless of n."""
    windows = cycles.release_windows(events.RELEASES)
    all_ids = sorted({i for ids in CLASS_IDS.values() for i in ids})
    series_by_id = load_series(store, all_ids)

    out = []
    for cls, ids in CLASS_IDS.items():
        cyc = class_cycles(ids, series_by_id, windows, through=today)
        out.append(
            {
                "class": cls,
                "cycles": cyc,
                "hit_rate": cycles.hit_rate(cyc),
                "confidence": score.confidence(cyc) if cyc else "low",
                "coverage": {
                    "launches_priced": len(cyc),
                    "launches_total": len(windows),
                    "ids_total": len(ids),
                },
            }
        )
    return out


def main():
    import os
    from datetime import datetime, timezone

    from collector.s3store import Store

    store = Store(os.environ["S3_BUCKET"])
    today = datetime.now(timezone.utc).date()
    classes = build_classes(store, today)

    artifact = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "through": today.isoformat(),
        "classes": classes,
    }
    store.put_json_gz(RELEASES_KEY, artifact)
    for c in classes:
        cov = c["coverage"]
        print(
            f"{c['class']}: {cov['launches_priced']} of {cov['launches_total']} "
            f"launches priced ({cov['ids_total']} ids), confidence {c['confidence']}"
        )
    print(f"release study -> s3://{store.bucket}/{RELEASES_KEY}")


if __name__ == "__main__":
    main()
