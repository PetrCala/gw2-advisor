"""GW2 commerce API client.

Fetches the full trading post price table (~27k items, 200 ids per request).
All-or-nothing: any page that fails after retries raises, so a snapshot is
either complete or not written at all. A missed snapshot is fine; a partial
one would corrupt the delta chain.
"""

import time

import requests

PRICES_URL = "https://api.guildwars2.com/v2/commerce/prices"
PAGE_SIZE = 200
RETRIES = 3


def fetch_all_prices(session=None):
    """Return {item_id: [buy_price, buy_qty, sell_price, sell_qty]}."""
    s = session or requests.Session()
    ids = _get_json(s, PRICES_URL)
    items = {}
    for i in range(0, len(ids), PAGE_SIZE):
        chunk = ids[i : i + PAGE_SIZE]
        rows = _get_json(s, PRICES_URL, params={"ids": ",".join(map(str, chunk))})
        for r in rows:
            items[r["id"]] = [
                r["buys"]["unit_price"],
                r["buys"]["quantity"],
                r["sells"]["unit_price"],
                r["sells"]["quantity"],
            ]
    return items


def _get_json(s, url, params=None):
    for attempt in range(RETRIES):
        try:
            resp = s.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            if attempt == RETRIES - 1:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"rate limited after {RETRIES} attempts: {url}")
