"""Bankroll and per-strategy allocation caps (docs/INVESTING.md, risk rule 1).

The bankroll is one number the user maintains: investable gold, kept out of
the repo and off the public site. It is read from BANKROLL_G (whole gold),
set locally in .env and in CI as a repository Actions variable. Unset means
the caps render as percentages only.

Caps are the design's allocation limits per strategy rung, binding at entry
time once strategies emit positions (M6.b+). Flips are absent on purpose:
their capital is self-limiting by construction.
"""

import os

# Share of bankroll each strategy rung may hold at entry time.
CAPS = {
    "release basket": 0.30,
    "festival carry": 0.25,
    "luxury desk": 0.25,
    "vault holds": 0.15,
    "corners": 0.10,
}

GOLD = 10_000  # copper per gold


def bankroll_copper(env=None):
    """The configured bankroll in copper, or None when unset or unparsable."""
    raw = (env or os.environ).get("BANKROLL_G", "").strip()
    if not raw:
        return None
    try:
        gold = int(float(raw))
    except ValueError:
        return None
    return gold * GOLD if gold > 0 else None


def cap_table(bankroll=None):
    """[{strategy, cap_pct, cap_copper}] rows; cap_copper None without a bankroll."""
    return [
        {
            "strategy": name,
            "cap_pct": share,
            "cap_copper": int(bankroll * share) if bankroll else None,
        }
        for name, share in CAPS.items()
    ]
