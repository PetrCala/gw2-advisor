from datetime import datetime, timedelta, timezone

import pytest

from collector import collect
from collector.gw2api import TransientError

NOW = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)


class _Store:
    """Only the one method skip_if_fresh reaches for."""

    def __init__(self, state):
        self._state = state

    def get_json_gz(self, key):
        if isinstance(self._state, Exception):
            raise self._state
        return self._state


def _state(age):
    ts = (NOW - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"ts": ts, "type": "full", "items": {}}


def _err():
    return TransientError("403 from the edge")


def test_fresh_state_skips_the_poll(capsys):
    collect.skip_if_fresh(_Store(_state(timedelta(minutes=20))), NOW, _err())
    assert "skipping this poll" in capsys.readouterr().out


def test_state_just_inside_the_limit_skips():
    collect.skip_if_fresh(_Store(_state(collect.STALE_AFTER - timedelta(minutes=1))), NOW, _err())


def test_stale_state_still_fails():
    store = _Store(_state(collect.STALE_AFTER + timedelta(minutes=1)))
    with pytest.raises(TransientError):
        collect.skip_if_fresh(store, NOW, _err())


def test_no_state_at_all_still_fails():
    with pytest.raises(TransientError):
        collect.skip_if_fresh(_Store(None), NOW, _err())


def test_unparseable_timestamp_still_fails():
    with pytest.raises(TransientError):
        collect.skip_if_fresh(_Store({"ts": "not a date", "items": {}}), NOW, _err())


def test_missing_timestamp_still_fails():
    with pytest.raises(TransientError):
        collect.skip_if_fresh(_Store({"items": {}}), NOW, _err())


def test_storage_failure_is_not_swallowed():
    # an unreadable bucket is our problem, not the API's; it must not look like a skip
    store = _Store(RuntimeError("s3 down"))
    with pytest.raises(RuntimeError, match="s3 down"):
        collect.skip_if_fresh(store, NOW, _err())


def test_main_skips_without_writing(monkeypatch, capsys):
    """The whole path: a transient fetch failure writes nothing and exits clean."""

    class _RecordingStore(_Store):
        def __init__(self):
            super().__init__(_state(timedelta(minutes=5)))
            self.writes = []

        def put_json_gz(self, key, body):
            self.writes.append(key)

    store = _RecordingStore()
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setattr(collect, "Store", lambda bucket: store)
    monkeypatch.setattr(collect.gw2api, "fetch_all_prices", _raise_transient)

    collect.main()

    assert store.writes == []
    assert "skipping this poll" in capsys.readouterr().out


def _raise_transient():
    raise TransientError("403 from the edge")
