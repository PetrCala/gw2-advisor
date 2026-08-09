"""Launch-risk classifier (S1/S2 interaction, docs/INVESTING.md risk item 4):
flags a farmable-material position open when an expansion launch is
imminent - new content also adds faucets, and post-release farms crash the
same materials the release basket and festival carry hold.

Pure and stateless, like season/actions.py: no S3 artifact, no workflow
step of its own. report/track.py calls launch_risk() fresh at render time
for every open position, the same "recompute every build, never trust a
stale stored field" convention season.actions.enrich and
report.build.add_exit_floor already use.

FARMABLE_CLASSES includes festival on invest/tags.py's own docstring
framing of t6/festival/staples as one repricing-risk family; that is a
judgment call from this module's own reading, not verified game history -
flag back if festival's re-supply risk should stay purely calendar-driven
(S3) instead.
"""

from datetime import timedelta

from invest import tags
from season import events
from season.actions import OPENS_SOON_DAYS as LAUNCH_WARN_DAYS

FARMABLE_CLASSES = frozenset({"t6", "festival", "staples"})


def launch_risk(item_id, today, warn_days=LAUNCH_WARN_DAYS):
    """(label, days_until) for the nearest expansion launch inside
    `warn_days` if `item_id` is in a farmable class; None otherwise, or
    without an item id at all. No sell_price is needed: only the
    explicit-id branch of invest.tags.classify matters here."""
    if item_id is None:
        return None
    if tags.classify(item_id) not in FARMABLE_CLASSES:
        return None
    found = events.next_release(today, today + timedelta(days=warn_days))
    if not found:
        return None
    launch_date, label = found
    return label, (launch_date - today).days
