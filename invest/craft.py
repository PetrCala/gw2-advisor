"""Time-gated crafting carry: daily spread over inputs for the five
account-capped dailies (docs/INVESTING.md, S8).

Four of the five materials the daily reset gates (Lump of Mithrillium, Glob
of Elder Spirit Residue, Spool of Thick Elonian Cord, Spool of Silk Weaving
Thread) are themselves account-bound: the reset caps how many you can craft,
not what you can buy or sell. Each refines, uncapped, into one Ascended
Refinement (tier 7) material that IS tradable (Deldrimor Steel Ingot,
Spiritwood Plank, Elonian Leather Square and Bolt of Damask respectively),
so the account-wide daily cap really rations one unit of that tradable
material per account per day: buy the tradable inputs for both refining
steps, craft the bound intermediate, refine it straight into the tradable
output, sell.

The fifth, Charged Quartz Crystal, is also account-bound and daily-capped,
but has no single downstream: 21 different recipes consume it (celestial
gear across every slot), so there is no one tradable item to price a spread
against. It is tracked for its input cost only; revenue, spread and dead
stay None for it rather than guessed.

Recipe ingredients were read from the live /v2/recipes API on 2026-08-08
(the archived gw2profits dump predates these items and does not carry
them); names in the comments are for the reader, the ids are what counts.

Pricing follows the same queue-front convention as scorers.flip: inputs are
bought via an order 1 above the current best bid, the output sold via a
listing 1 below the current best ask, net of the 15% cut. These are bulk
staple materials (ectoplasm, basic-tier ingots/planks/leathers/cloth), deep
and liquid enough that a resting order fills same-day, so this is a fair
model for a once-a-day action rather than the optimistic case.

Spread is the net-of-tax markup over cost; a refinement is "dead" once that
drops under the 15% tax itself (a little above pure breakeven, matching the
hurdle S8 states directly), rather than at zero. The daily point is
appended to invest/craft.json.gz so report.invest and report.track can show
whether a carry is live without recomputing history.

Usage:
    python -m invest.craft    # needs S3_BUCKET
"""

import os
import sys
from datetime import datetime, timezone

from account.pnl import net_of_fees
from scorers.flip import TAX

CRAFT_KEY = "invest/craft.json.gz"
MAX_POINTS = 2000  # about five and a half years of daily points

# {key, name, output_id, inputs}. inputs cover both refining steps' tradable
# materials (the account-bound intermediate itself is never bought).
# output_id is None for Charged Quartz Crystal: no single tradable next step.
CHAINS = (
    {
        "key": "mithrillium",
        "name": "Lump of Mithrillium -> Deldrimor Steel Ingot",
        "output_id": 46738,  # Deldrimor Steel Ingot
        "inputs": (
            (19684, 50),  # Mithril Ingot
            (19721, 1),  # Glob of Ectoplasm
            (46747, 10),  # Thermocatalytic Reagent
            (19683, 20),  # Iron Ingot
            (19688, 10),  # Steel Ingot
            (19681, 20),  # Darksteel Ingot
        ),
    },
    {
        "key": "elder_spirit",
        "name": "Glob of Elder Spirit Residue -> Spiritwood Plank",
        "output_id": 46736,  # Spiritwood Plank
        "inputs": (
            (19709, 50),  # Elder Wood Plank
            (19721, 1),  # Glob of Ectoplasm
            (46747, 10),  # Thermocatalytic Reagent
            (19713, 20),  # Soft Wood Plank
            (19714, 10),  # Seasoned Wood Plank
            (19711, 20),  # Hard Wood Plank
        ),
    },
    {
        "key": "elonian_cord",
        "name": "Spool of Thick Elonian Cord -> Elonian Leather Square",
        "output_id": 46739,  # Elonian Leather Square
        "inputs": (
            (19735, 50),  # Cured Thick Leather Square
            (19721, 1),  # Glob of Ectoplasm
            (46747, 10),  # Thermocatalytic Reagent
            (19733, 20),  # Cured Thin Leather Square
            (19734, 10),  # Cured Coarse Leather Square
            (19736, 20),  # Cured Rugged Leather Square
        ),
    },
    {
        "key": "silk_weaving",
        "name": "Spool of Silk Weaving Thread -> Bolt of Damask",
        "output_id": 46741,  # Bolt of Damask
        "inputs": (
            (19747, 100),  # Bolt of Silk
            (19721, 1),  # Glob of Ectoplasm
            (19790, 25),  # Spool of Gossamer Thread
            (19740, 20),  # Bolt of Wool
            (19742, 10),  # Bolt of Cotton
            (19744, 20),  # Bolt of Linen
        ),
    },
    {
        "key": "charged_quartz",
        "name": "Charged Quartz Crystal",
        "output_id": None,  # 21 recipes consume it; no single tradable next step
        "inputs": (
            (43773, 25),  # Quartz Crystal
        ),
    },
)


def all_ids():
    """Every tradable id a chain reads: inputs plus priced outputs."""
    ids = set()
    for c in CHAINS:
        ids.update(i for i, _ in c["inputs"])
        if c["output_id"] is not None:
            ids.add(c["output_id"])
    return sorted(ids)


def cost_of(chain, prices):
    """Copper to buy every input once, queue-front (bid + 1); None if any
    input has no live two-sided market."""
    total = 0
    for item_id, qty in chain["inputs"]:
        p = prices.get(item_id)
        if not p or not p[0] or not p[1]:
            return None
        total += qty * (p[0] + 1)
    return total


def spread_of(chain, prices):
    """{key, name, output_id, cost, revenue, spread, dead} for one chain.

    revenue, spread and dead stay None without a priced, single tradable
    output (always true for Charged Quartz Crystal); cost stays None if any
    input lacks a live market. dead flags a spread under the 15% tax itself,
    the hurdle S8 states directly.
    """
    cost = cost_of(chain, prices)
    revenue = spread = dead = None
    if cost and chain["output_id"] is not None:
        out = prices.get(chain["output_id"])
        if out and out[0] and out[1]:
            revenue = net_of_fees(out[1] - 1)
            spread = round((revenue - cost) / cost, 4)
            dead = spread < TAX
    return {
        "key": chain["key"],
        "name": chain["name"],
        "output_id": chain["output_id"],
        "cost": cost,
        "revenue": revenue,
        "spread": spread,
        "dead": dead,
    }


def build_point(prices, today):
    """One day's reading across every chain."""
    return {"d": today.isoformat(), "chains": [spread_of(c, prices) for c in CHAINS]}


def append(series, point, cap=MAX_POINTS):
    """The series with today's point added, one per day, oldest dropped."""
    points = [p for p in (series.get("points") or []) if p.get("d") != point["d"]]
    points.append(point)
    points.sort(key=lambda p: p["d"])
    return {"points": points[-cap:]}


def main():
    from collector import gw2api
    from collector.s3store import Store

    raw = gw2api.fetch_prices(all_ids())
    prices = {i: (v[0], v[2]) for i, v in raw.items()}
    today = datetime.now(timezone.utc).date()
    point = build_point(prices, today)

    store = Store(os.environ["S3_BUCKET"])
    series = append(store.get_json_gz(CRAFT_KEY) or {}, point)
    store.put_json_gz(CRAFT_KEY, series)

    live = sum(1 for c in point["chains"] if c["dead"] is False)
    priced = sum(1 for c in point["chains"] if c["dead"] is not None)
    print(
        f"craft carry {point['d']}: {live} of {priced} priced refinements live "
        f"({len(point['chains'])} tracked) -> s3://{store.bucket}/{CRAFT_KEY}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
