"""Thesis registry (M6.d, docs/INVESTING.md strategy S2): parse, validate
and enrich the hand-edited item-leg convictions in invest/theses.yaml.

The thesis is automation cannot read patch notes, so a human stays in the
loop: invest/theses.yaml is a small, git-reviewed YAML list recording item
id, direction, the catalyst and its date, an entry ceiling, a target, an
invalidation condition and date, and a capital size. This module does
everything else: infers a live bucket from the calendar and the fresh
snapshot price, and, once a leg is actually actionable (bucket == "active"),
sizes it against the live buy book the same way season/actions.py bounds a
seasonal pick's exit risk.

Buckets, in priority order:

- closed: the human set status: closed. The real in-game decision has
  already been made; this is how the registry records that, never how one
  is placed.
- invalidated: today is past invalidation_date, the hard date that forces
  the exit decision whether or not the invalidation prose has technically
  been confirmed.
- catalyst_passed: today is past catalyst_date and the position is not
  otherwise resolved; the target may or may not have been hit, but either
  way the catalyst itself has come and gone and the human needs to decide
  hold or invalidate.
- active: an explicit status: active override, or (direction == "long"
  only) the live price is at or under entry_ceiling.
- watching: the default, before any of the above fires.

"avoid" entries flow through the same bucket machinery (there is a
catalyst and a calendar to track) but never get a qty or a capital
figure: nothing is ever sized or paper-tracked for a direction to stay
away from, only surfaced as a notify-only warning.

Usage:
    python -m invest.thesis    # local read-only dry run, no S3 write

Pure and stateless, like season/actions.py: no S3 artifact of its own, no
daily workflow step. report/build.py imports load_theses()/enrich_all() at
report-build time so the registry always renders against the fresh
snapshot rather than a stale copy.
"""

from datetime import date
from pathlib import Path

from invest import bankroll
from season.actions import bounded_qty, exit_pct

STRATEGY = "thesis registry"  # matches invest.bankroll.CAPS's key; checked by a test
THESIS_HORIZON_DAYS = 45  # paper-tracker safety-net backstop (report/track.py)

DEFAULT_PATH = Path(__file__).resolve().parent / "theses.yaml"

BUCKETS = ("watching", "active", "catalyst_passed", "invalidated", "closed")

_REQUIRED_COMMON = (
    "key", "id", "name", "direction", "catalyst", "catalyst_date",
    "invalidation", "invalidation_date", "opened",
)
_REQUIRED_LONG = ("entry_ceiling", "target", "size")
_STATUSES = ("watching", "active", "closed", None)


def _validate(entry):
    """The entry, shallow-copied, with every required field present and
    every date field parseable; raises ValueError/TypeError otherwise."""
    if not isinstance(entry, dict):
        raise TypeError("entry is not a mapping")
    missing = [f for f in _REQUIRED_COMMON if entry.get(f) in (None, "")]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")
    direction = entry["direction"]
    if direction not in ("long", "avoid"):
        raise ValueError(f"direction must be 'long' or 'avoid', got {direction!r}")
    if direction == "long":
        missing = [f for f in _REQUIRED_LONG if entry.get(f) in (None, "")]
        if missing:
            raise ValueError(f"long entry missing required field(s): {', '.join(missing)}")
    for field in ("catalyst_date", "invalidation_date", "opened"):
        date.fromisoformat(str(entry[field]))
    status = entry.get("status")
    if status not in _STATUSES:
        raise ValueError(f"status must be watching/active/closed/null, got {status!r}")
    return dict(entry)


def load_theses(path=DEFAULT_PATH):
    """Every valid entry in the registry at `path`; a malformed one is
    skipped with a printed warning rather than crashing the whole build.
    """
    import yaml

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        print(f"invest.thesis: cannot read {path}: {e}")
        return []
    doc = yaml.safe_load(text) or {}
    raw = doc.get("theses") or []
    out = []
    for i, entry in enumerate(raw):
        label = entry.get("key", f"#{i}") if isinstance(entry, dict) else f"#{i}"
        try:
            out.append(_validate(entry))
        except (KeyError, ValueError, TypeError) as e:
            print(f"invest.thesis: skipping malformed entry {label}: {e}")
    return out


def _bucket(entry, today, price):
    status = entry.get("status")
    catalyst_date = date.fromisoformat(entry["catalyst_date"])
    invalidation_date = date.fromisoformat(entry["invalidation_date"])
    if status == "closed":
        return "closed"
    if today > invalidation_date:
        return "invalidated"
    if today > catalyst_date:
        return "catalyst_passed"
    if status == "active":
        return "active"
    if entry["direction"] == "long" and price is not None and price <= entry["entry_ceiling"]:
        return "active"
    return "watching"


def enrich(row, today, price=None, buys=None):
    """`row` (one validated registry entry) enriched with its live bucket,
    price, distance-to-ceiling/target, days-to-catalyst/invalidation and,
    once bucket == "active" on a long leg, qty/capital sized against
    `buys` (the live buy book) via season.actions.bounded_qty/exit_pct -
    the same exit-floor discipline apply_exit_floor uses for seasonal
    picks, reused rather than that function itself since it is coupled to
    seasonal-pick field names. Returns a new dict; `row` is not mutated.
    """
    out = dict(row)
    catalyst_date = date.fromisoformat(row["catalyst_date"])
    invalidation_date = date.fromisoformat(row["invalidation_date"])
    bucket = _bucket(row, today, price)

    ceiling = row.get("entry_ceiling")
    target = row.get("target")
    dist_to_ceiling = round(price / ceiling - 1, 4) if price and ceiling else None
    dist_to_target = round(price / target - 1, 4) if price and target else None

    out.update(
        bucket=bucket,
        live_price=price,
        dist_to_ceiling=dist_to_ceiling,
        dist_to_target=dist_to_target,
        days_to_catalyst=(catalyst_date - today).days,
        days_to_invalidation=(invalidation_date - today).days,
    )

    if row["direction"] != "long" or bucket != "active":
        out["qty"] = None
        out["capital"] = None
        out["exit_pct"] = None
        return out

    base_qty = int(row["size"] // price) if price else 0
    qty = bounded_qty(buys, base_qty, price) if base_qty else 0
    out["qty"] = qty or None
    out["capital"] = qty * price if qty else None
    out["exit_pct"] = exit_pct(buys, qty, price) if buys and qty else None
    return out


def enrich_all(theses, today, fresh_by_id, books=None):
    """enrich() every entry against `fresh_by_id` ({item_id: sell_price},
    the same mapping report.build already assembles from the snapshot) and
    `books` ({item_id: {"buys": ..., "sells": ...}}, only fetched for
    watching/active entries by report.build); returns a new list."""
    fresh_by_id = fresh_by_id or {}
    books = books or {}
    out = []
    for row in theses:
        price = fresh_by_id.get(row["id"])
        buys = (books.get(row["id"]) or {}).get("buys")
        out.append(enrich(row, today, price, buys))
    return out


def main():
    from datetime import datetime, timezone

    from collector import dw2

    theses = load_theses()
    today = datetime.now(timezone.utc).date()
    if not theses:
        print("thesis registry: 0 entries")
        return

    snap = dw2.fetch_snapshot()
    fresh_by_id = {
        it.get("id"): it.get("sell_price") for it in snap if it.get("id") is not None
    }
    enriched = enrich_all(theses, today, fresh_by_id)

    counts = {}
    for r in enriched:
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1
    summary = ", ".join(f"{counts[b]} {b}" for b in BUCKETS if counts.get(b))

    # This repo's public Actions logs must never carry absolute gold (the
    # nav.py-class privacy bug this repo's CLAUDE.md documents): committed
    # size against the private bankroll cap prints as a share only.
    committed = sum(r["capital"] for r in enriched if r.get("capital"))
    bankroll_c = bankroll.bankroll_copper()
    cap_line = "bankroll unset; committed share of the cap unknown"
    if bankroll_c:
        cap_copper = bankroll_c * bankroll.CAPS[STRATEGY]
        cap_line = f"committed size is {committed / cap_copper:.0%} of the thesis registry cap"
    print(f"thesis registry: {len(theses)} entries ({summary}); {cap_line}")


if __name__ == "__main__":
    main()
