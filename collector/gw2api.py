"""GW2 commerce API client.

Fetches the full trading post price table (~27k items, 200 ids per request).
All-or-nothing: any page that fails after retries raises, so a snapshot is
either complete or not written at all. A missed snapshot is fine; a partial
one would corrupt the delta chain.

These endpoints are public, so a 403 is never about credentials: the API's
edge just blips on cloud IPs from time to time and clears within a minute.
It is retried like a rate limit rather than treated as a refusal, because
the old three-attempt, 15-second budget gave up well inside that window.
"""

import time

import requests

PRICES_URL = "https://api.guildwars2.com/v2/commerce/prices"
LISTINGS_URL = "https://api.guildwars2.com/v2/commerce/listings"
PAGE_SIZE = 200
RETRIES = 5
BACKOFF = 5  # seconds, times the attempt number: 50s of waiting across 5 tries
USER_AGENT = "gw2-advisor (github.com/PetrCala/gw2-advisor)"
TRANSIENT_STATUS = (403, 429, 500, 502, 503, 504)


class TransientError(requests.RequestException):
    """Upstream refused us or went unreachable in a way that clears on its own.

    Kept distinct from a genuine 4xx so a caller can decide that skipping the
    work is better than failing at it. collector.collect does exactly that
    while its stored snapshot is still fresh; everything else lets it
    propagate, which is the same failure they got before this existed.
    """


def _session(session=None):
    if session is not None:
        return session
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def fetch_tradable_ids(session=None):
    """Return the sorted list of all tradable item ids."""
    s = _session(session)
    return sorted(_get_json(s, PRICES_URL))


def fetch_all_prices(session=None):
    """Return {item_id: [buy_price, buy_qty, sell_price, sell_qty]}."""
    s = _session(session)
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


def fetch_prices(item_ids, session=None):
    """Return {item_id: [buy_price, buy_qty, sell_price, sell_qty]} for just
    `item_ids`, the same shape as fetch_all_prices but scoped to a small,
    known set (invest.craft's recipe inputs) instead of the whole ~27k
    table. Ids with no commerce listing (account-bound materials, mainly)
    are simply absent from the result.
    """
    s = _session(session)
    items = {}
    ids = list(item_ids)
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


def fetch_listings(item_ids, session=None):
    """Return {item_id: {"buys": [[price, qty], ...], "sells": [[price, qty], ...]}}.

    buys come sorted best (highest) first, sells best (lowest) first, as the
    API returns them. Items without any listings are absent from the result.
    """
    s = _session(session)
    books = {}
    ids = list(item_ids)
    for i in range(0, len(ids), PAGE_SIZE):
        chunk = ids[i : i + PAGE_SIZE]
        rows = _get_json(s, LISTINGS_URL, params={"ids": ",".join(map(str, chunk))})
        for r in rows:
            books[r["id"]] = {
                "buys": [[lv["unit_price"], lv["quantity"]] for lv in r.get("buys", [])],
                "sells": [[lv["unit_price"], lv["quantity"]] for lv in r.get("sells", [])],
            }
    return books


def _get_json(s, url, params=None):
    last = "no attempt made"
    for attempt in range(1, RETRIES + 1):
        try:
            resp = s.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        else:
            if resp.status_code not in TRANSIENT_STATUS:
                resp.raise_for_status()
                return resp.json()
            last = f"HTTP {resp.status_code}, body {_body(resp)}"
        if attempt < RETRIES:
            time.sleep(BACKOFF * attempt)
    raise TransientError(f"{url} unavailable after {RETRIES} attempts, last: {last}")


def _body(resp):
    """A one-line excerpt of a failed response, so the logs say who refused us.

    A 403 from the edge and a 403 from the API itself look identical in a
    status line and want different fixes; the body distinguishes them.
    """
    try:
        text = " ".join(resp.text.split())
    except Exception:  # a body we cannot even decode is still worth reporting
        return "<unreadable>"
    return text[:200] if text else "<empty>"
