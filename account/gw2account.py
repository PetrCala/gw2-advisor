"""Authenticated GW2 account API client.

Needs a key with the account, wallet, inventories, characters and tradingpost
scopes; make one at https://account.arena.net/applications. The key is read
from GW2_API_KEY and never printed.

Everything here is read-only. The account endpoints are cached five minutes
server side, so polling faster than that returns the same bytes.
"""

import os
import time

import requests

BASE = "https://api.guildwars2.com/v2"
PAGE_SIZE = 200
RETRIES = 3

# Transaction history is capped at 90 days by the API.
HISTORY_DAYS = 90


class MissingKey(RuntimeError):
    pass


def key_from_env():
    key = os.environ.get("GW2_API_KEY", "").strip()
    if not key:
        raise MissingKey(
            "GW2_API_KEY is not set. Create a key with the account, wallet, "
            "inventories, characters and tradingpost scopes at "
            "https://account.arena.net/applications and put it in .env"
        )
    return key


class Account:
    def __init__(self, key=None, session=None):
        self.key = key or key_from_env()
        self.s = session or requests.Session()
        self.s.headers["Authorization"] = f"Bearer {self.key}"

    def get(self, path, params=None):
        """GET an authenticated /v2 path, e.g. "/account/bank"."""
        return _get_json(self.s, BASE + path, params)

    def token_info(self):
        """Key name and granted scopes, for checking a key before using it."""
        return self.get("/tokeninfo")

    def wallet(self):
        """{currency_id: value}. Currency 1 is coin, in copper."""
        return {c["id"]: c["value"] for c in self.get("/account/wallet")}

    def coins(self):
        return self.wallet().get(1, 0)

    def delivery(self):
        """Trading post pickup box: {"coins": n, "items": [{"id", "count"}]}."""
        return self.get("/commerce/delivery")

    def current_orders(self, side):
        """Open buy or sell orders; side is "buys" or "sells"."""
        return self._get_paged(f"/commerce/transactions/current/{side}")

    def history(self, side, since=None, max_pages=50):
        """Completed buy or sell transactions, newest first.

        The endpoint returns newest first, so paging stops at the first page
        holding nothing newer than `since`. max_pages is a runaway guard: hitting
        it means the window was not fully covered, so the caller is told.
        """
        rows, page, truncated = [], 0, False
        while True:
            if page >= max_pages:
                truncated = True
                break
            batch = self.get(
                f"/commerce/transactions/history/{side}",
                params={"page": page, "page_size": PAGE_SIZE},
            )
            if not batch:
                break
            rows.extend(batch)
            if since is not None and all(r["purchased"] < since for r in batch):
                break
            if len(batch) < PAGE_SIZE:
                break
            page += 1
        return rows, truncated

    def _get_paged(self, path):
        rows, page = [], 0
        while True:
            batch = self.get(path, params={"page": page, "page_size": PAGE_SIZE})
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                return rows
            page += 1


def _get_json(s, url, params=None):
    for attempt in range(RETRIES):
        try:
            resp = s.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            if resp.status_code in (401, 403):
                raise PermissionError(
                    f"{url} rejected the key ({resp.status_code}); it is likely "
                    "missing a scope. Check with token_info()."
                )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            if attempt == RETRIES - 1:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"rate limited after {RETRIES} attempts: {url}")
