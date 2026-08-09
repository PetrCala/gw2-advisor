from datetime import date, timedelta

from invest import risk, tags
from season import events


def _next_release_from(today):
    """The nearest upcoming RELEASES date on/after `today`, for a
    warn-window-relative test that does not hardcode a specific launch."""
    return min(
        date.fromisoformat(iso) for iso, _ in events.RELEASES if date.fromisoformat(iso) >= today
    )


def test_t6_id_flags_inside_the_warn_window():
    item_id = next(iter(tags.T6))
    launch = _next_release_from(date(2020, 1, 1))
    today = launch - timedelta(days=10)
    got = risk.launch_risk(item_id, today, warn_days=30)
    assert got is not None
    label, days_until = got
    assert days_until == 10


def test_staples_id_flags_inside_the_warn_window():
    item_id = next(iter(tags.STAPLES))
    launch = _next_release_from(date(2020, 1, 1))
    today = launch - timedelta(days=5)
    assert risk.launch_risk(item_id, today, warn_days=30) is not None


def test_luxury_and_other_ids_never_flag():
    launch = _next_release_from(date(2020, 1, 1))
    today = launch - timedelta(days=5)
    assert risk.launch_risk(999_999_999, today, warn_days=30) is None  # "other"


def test_outside_the_warn_window_does_not_flag():
    item_id = next(iter(tags.T6))
    launch = _next_release_from(date(2020, 1, 1))
    today = launch - timedelta(days=200)
    assert risk.launch_risk(item_id, today, warn_days=30) is None


def test_flag_clears_the_day_after_launch():
    item_id = next(iter(tags.T6))
    launch = _next_release_from(date(2020, 1, 1))
    assert risk.launch_risk(item_id, launch + timedelta(days=1), warn_days=30) is None


def test_missing_item_id_never_flags():
    assert risk.launch_risk(None, date(2026, 1, 1)) is None


def test_default_warn_days_matches_opens_soon():
    from season.actions import OPENS_SOON_DAYS

    assert risk.LAUNCH_WARN_DAYS == OPENS_SOON_DAYS
