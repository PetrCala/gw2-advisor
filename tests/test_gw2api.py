import pytest
import requests

from collector import gw2api


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for url", response=self)


class _Session:
    """Hands back canned responses in order and records what was asked for."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(gw2api.time, "sleep", lambda _: None)


def test_transient_403_is_retried():
    s = _Session(_Resp(403), _Resp(403), _Resp(200, [1, 2]))
    assert gw2api._get_json(s, gw2api.PRICES_URL) == [1, 2]
    assert len(s.calls) == 3


def test_rate_limit_is_retried():
    s = _Session(_Resp(429), _Resp(200, [7]))
    assert gw2api._get_json(s, gw2api.PRICES_URL) == [7]
    assert len(s.calls) == 2


def test_persistent_403_still_raises():
    s = _Session(*[_Resp(403) for _ in range(gw2api.RETRIES)])
    with pytest.raises(requests.HTTPError):
        gw2api._get_json(s, gw2api.PRICES_URL)
    assert len(s.calls) == gw2api.RETRIES


def test_404_is_not_retried():
    s = _Session(_Resp(404))
    with pytest.raises(requests.HTTPError):
        gw2api._get_json(s, gw2api.PRICES_URL)
    assert len(s.calls) == 1


def test_connection_error_is_retried():
    class _Flaky(_Session):
        def get(self, url, params=None, timeout=None):
            self.calls.append((url, params))
            if len(self.calls) == 1:
                raise requests.ConnectionError("reset")
            return _Resp(200, [3])

    s = _Flaky()
    assert gw2api._get_json(s, gw2api.PRICES_URL) == [3]
    assert len(s.calls) == 2


def test_default_session_identifies_itself():
    assert gw2api._session().headers["User-Agent"] == gw2api.USER_AGENT


def test_caller_session_is_used_as_given():
    s = _Session()
    assert gw2api._session(s) is s


def test_fetch_all_prices_pages_and_flattens():
    ids = list(range(gw2api.PAGE_SIZE + 3))
    page = [{"id": 1, "buys": {"unit_price": 10, "quantity": 2},
             "sells": {"unit_price": 20, "quantity": 4}}]
    s = _Session(_Resp(200, ids), _Resp(200, page), _Resp(200, []))
    assert gw2api.fetch_all_prices(s) == {1: [10, 2, 20, 4]}
    # id listing, then one request per page of PAGE_SIZE
    assert len(s.calls) == 3
    assert s.calls[2][1]["ids"].split(",") == [str(i) for i in ids[gw2api.PAGE_SIZE:]]
