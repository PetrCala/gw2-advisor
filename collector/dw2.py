"""datawars2.ie API client (community mirror of TP data, history since 2012).

Feeds two consumers: the one-time multi-year backfill (collector/backfill.py)
and the daily item snapshot (names plus rolling fill aggregates) read by the
flip scorer. It is a free community service run by Silvers; keep batches
small and requests throttled.

Field semantics worth remembering: buy_sold counts units filled against buy
orders (the side we buy on), sell_sold counts units bought off sell listings
(the side we sell on). Both are derived from listing deltas on their side.
"""

import csv
import io
import time

import requests

BASE = "https://api.datawars2.ie/gw2"
RETRIES = 3

# One row per marketable item, refreshed by the mirror roughly hourly.
SNAPSHOT_FIELDS = (
    "id,name,rarity,vendor_value,buy_price,sell_price,buy_quantity,sell_quantity,"
    "1d_buy_sold,1d_sell_sold,7d_buy_sold,7d_sell_sold,"
    "7d_buy_price_avg,7d_sell_price_avg,7d_sell_price_min,7d_sell_price_max,"
    "1m_buy_sold,1m_sell_sold"
)

# Daily history columns kept by the backfill: prices, fills, churn, queue depth.
# Rows before ~2019 carry only price/quantity extremes; the rest stay null.
HISTORY_FIELDS = (
    "date,itemID,buy_price_avg,buy_price_min,buy_price_max,"
    "sell_price_avg,sell_price_min,sell_price_max,"
    "buy_sold,sell_sold,buy_listed,sell_listed,buy_delisted,sell_delisted,"
    "buy_quantity_avg,sell_quantity_avg"
)


def fetch_snapshot(session=None):
    """Return the marketable-item snapshot as a list of dicts."""
    s = session or requests.Session()
    resp = _get(
        s,
        f"{BASE}/v1/items/json",
        params={"fields": SNAPSHOT_FIELDS, "filter": "marketable:true"},
    )
    return resp.json()


def fetch_history(item_ids, session=None):
    """Return full daily history rows (list of dicts) for the given ids.

    Batches much above 10 ids make the server 502. On a failed batch the
    request splits in half down to single ids, so one heavy id cannot sink
    a whole chunk; a single id that still fails after retries raises.
    """
    s = session or requests.Session()
    try:
        resp = _get(
            s,
            f"{BASE}/v2/history/csv",
            params={"itemID": ",".join(map(str, item_ids)), "fields": HISTORY_FIELDS},
        )
    except requests.RequestException:
        if len(item_ids) == 1:
            raise
        mid = len(item_ids) // 2
        return fetch_history(item_ids[:mid], s) + fetch_history(item_ids[mid:], s)
    return list(csv.DictReader(io.StringIO(resp.text)))


def _get(s, url, params=None):
    for attempt in range(RETRIES):
        try:
            resp = s.get(url, params=params, timeout=120)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt == RETRIES - 1:
                raise
            time.sleep(10 * (attempt + 1))
