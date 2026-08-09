"""Structural bounds (M6.e part 1, docs/INVESTING.md strategy S7): the
conversion graph, the floor primitives, and the alternative-route ceiling
that S6's corner screen consumes.

Pure and stateless, like invest/risk.py: no S3 artifact, no workflow step of
its own, no stored series. Every caller parses the archive and recomputes
fresh at build time, the same "recompute every build, never trust a stale
stored field" convention season.actions.enrich and report.build.add_exit_floor
already use. Module-level imports are stdlib only, so importing this module
never drags in requests, boto3 or pyarrow.

The graph comes from archive/gw2profits/recipes_v3.json, the preserved
gw2profits dump (8,062 recipes, every id negative, meaning it is exactly the
complement of the official /v2/recipes table rather than a copy of it: it
carries the Mystic Forge conversions, merchant exchanges and container
double-clicks the official API never listed, and zero crafting-discipline
recipes). Achievement, Charge and Salvage rows are dropped here: they are
not repeatable purchasable routes, so they cannot bound a price.

THE CATALYST RULE, the load-bearing judgment call of this milestone.

Several forge and merchant routes consume account-bound, unsellable
catalysts (Philosopher's Stone, Mystic Crystal, Obsidian Shard, Bloodstone
Shard, Siege Master's Guide; all bought from a vendor for spirit shards,
so none has a trading post price). The community values a spirit shard by
what promotion returns, and promotion is priced off the shard, so any
constant we invented for it would close a loop rather than measure
anything. So no shard price is introduced at all, and each direction takes
its own conservative branch instead:

- For a CEILING, catalysts are priced at ZERO and a stochastic recipe is
  taken at its output-range MAXIMUM. Both make the alternative route as
  cheap as possible, so the ceiling comes out as LOW as possible, which
  means less headroom, fewer and smaller corners: the safe direction.
- For a FLOOR, any recipe touching a catalyst is EXCLUDED outright, and so
  is any stochastic recipe (an expectation is not a floor). A floor resting
  on free catalysts or on an average would overstate protection, which is
  the dangerous direction.

Deferred, named rather than dropped: salvage expectation as a third floor
source. No data for it exists in this repo. The archive's 53 Salvage rows
are legendary precursor Experiment recipes, not gear salvage, and the
archived gw2profits API docs show /json/v3 was its only endpoint, so the
published salvage rates never had a feed to preserve. Same for the
unidentified-gear leg S7's text names: it needs a container drop table that
exists in neither the archive nor /v2/recipes.

Deferred too: the /v2/recipes static build, which would add ordinary
craft-from-inputs ceilings. The archive alone covers the forge, merchant and
container routes S6's real corner targets (festival-gated, discontinued,
limited-faucet items) actually route through.
"""

import json
from pathlib import Path

from invest.luxury import EXCLUDED_IDS

ARCHIVE_PATH =Path(__file__).resolve().parent.parent / "archive" / "gw2profits" / "recipes_v3.json"

TAX = 0.15  # trading post cut; kept in sync with scorers.flip.TAX by a test

# Account-bound, unsellable Mystic Forge catalysts, every one bought from a
# vendor for spirit shards. Ids and names read straight out of the archive's
# own Merchant rows on 2026-08-09 (each has a negative ingredient id, which
# is how gw2profits encoded a currency rather than an item), and each appears
# as an ingredient in real conversion recipes. See the catalyst rule above
# for what they are priced at in each direction.
CATALYST_IDS = frozenset({
    20796,  # Philosopher's Stone
    20799,  # Mystic Crystal
    19925,  # Obsidian Shard
    20797,  # Bloodstone Shard
    20798,  # Siege Master's Guide
})

# Casefolded on purpose: the archive carries both "Double Click" (140 rows)
# and "Double click" (1 row), and an exact-match parser silently drops one.
USABLE_DISCIPLINES = frozenset({"mystic forge", "merchant", "double click"})

_SOURCES = {
    "mystic forge": "forge",
    "merchant": "merchant",
    "double click": "double-click",
}


# --- the graph ------------------------------------------------------------


def _counts(recipe):
    """(count_min, count_max) for one archive row, or None when unusable.

    output_item_count_range is "40-200" on a stochastic forge promotion and
    an empty string on a deterministic one, where output_item_count is the
    exact yield. On a stochastic row output_item_count is the archive's own
    expected value, which is neither bound, so the range wins when present.

    Fifteen rows carry a fractional count and no range (Endless Gift Dolyak
    Tonic at 0.33, a chance drop rather than a yield). They are dropped: a
    lottery is not a route, and a route is what bounds a price.
    """
    rng = (recipe.get("output_item_count_range") or "").strip()
    if rng:
        lo, _, hi = rng.partition("-")
        try:
            lo, hi = int(lo), int(hi)
        except ValueError:
            return None
        if lo > hi or hi <= 0:
            return None
        return lo, hi
    count = recipe.get("output_item_count")
    if not isinstance(count, int) or count <= 0:
        return None
    return count, count


def _source(recipe):
    """The normalized route name for one row, or None outside USABLE_DISCIPLINES."""
    for d in recipe.get("disciplines") or []:
        folded = d.casefold()
        if folded in USABLE_DISCIPLINES:
            return _SOURCES[folded]
    return None


def load_graph(path=ARCHIVE_PATH):
    """The conversion graph parsed out of the archive.

    {"by_output": {item_id: [recipe, ...]},
     "by_ingredient": {item_id: [recipe, ...]},
     "n_recipes": int, "skipped": {reason: count}}

    where a recipe is {output_id, count_min, count_max, ingredients:
    [(item_id, qty), ...], source} and source is forge / merchant /
    double-click. A missing or unreadable archive returns an empty graph
    rather than raising: callers that need a ceiling must then fail closed
    (invest.corner does), never fall back to a looser bound.
    """
    graph = {"by_output": {}, "by_ingredient": {}, "n_recipes": 0, "skipped": {}}

    def skip(reason):
        graph["skipped"][reason] = graph["skipped"].get(reason, 0) + 1

    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        graph["skipped"]["archive_unreadable"] = 1
        return graph

    for row in rows:
        source = _source(row)
        if not source:
            skip("discipline")
            continue
        output_id = row.get("output_item_id")
        if not isinstance(output_id, int) or output_id <= 0:
            skip("no_output")
            continue
        counts = _counts(row)
        if not counts:
            skip("bad_count")
            continue
        ingredients = [
            (g["item_id"], g["count"])
            for g in row.get("ingredients") or []
            if isinstance(g.get("item_id"), int) and (g.get("count") or 0) > 0
        ]
        if not ingredients:
            skip("no_ingredients")
            continue
        recipe = {
            "output_id": output_id,
            "count_min": counts[0],
            "count_max": counts[1],
            "ingredients": ingredients,
            "source": source,
        }
        graph["n_recipes"] += 1
        graph["by_output"].setdefault(output_id, []).append(recipe)
        for item_id, _ in ingredients:
            graph["by_ingredient"].setdefault(item_id, []).append(recipe)
    return graph


# --- price helpers --------------------------------------------------------
#
# `prices` throughout is {item_id: [buy_price, buy_qty, sell_price, sell_qty]},
# the shape collector.gw2api.fetch_prices and fetch_all_prices both return.


def _bid(prices, item_id):
    """Best bid with a live quantity behind it, or None."""
    p = prices.get(item_id)
    if not p or not p[0] or not p[1]:
        return None
    return p[0]


def _ask(prices, item_id):
    """Best ask with a live quantity behind it, or None."""
    p = prices.get(item_id)
    if not p or len(p) < 4 or not p[2] or not p[3]:
        return None
    return p[2]


# --- floors ---------------------------------------------------------------


def at_vendor_floor(sell_price, vendor_value, tax=TAX):
    """True when selling on the trading post nets no more than the vendor pays.

    The one copy of the repo's vendor-floor test. season/compute.py's
    prefilter and scorers/flip.py's data-glitch filter are deliberately left
    alone: importing invest from season would invert the existing dependency
    direction for zero behaviour change, and flip's check is a different test
    (no tax) whose live output would move if it were tax-adjusted.
    """
    if not sell_price or not vendor_value:
        return False
    return sell_price * (1 - tax) <= vendor_value


def vendor_floor(row):
    """The vendor buy-back price for one dw2 snapshot row, or None.

    Gross on purpose: a vendor sale carries no trading post cut, while the
    price a coverage ratio compares it against does. An item trading below
    its vendor value (a data glitch, the case scorers.flip filters) simply
    yields coverage at or above 1, which renders as "at floor" rather than
    as a negative distance.
    """
    return row.get("vendor_value") or None


def conversion_floor(item_id, prices, graph):
    """(copper, source) the fodder value of one unit of `item_id`, or
    (None, None).

    The most any conversion that CONSUMES this item can afford to pay for
    it: the output sold at the bid net of tax, minus every other ingredient
    bought at the ask, divided by how many units of this item the route
    eats. Clamped at zero, since a route that loses money bounds nothing.

    Excluded, per the catalyst rule in the module docstring: stochastic
    recipes, any recipe touching a catalyst, and any recipe whose output is
    on invest.luxury.EXCLUDED_IDS (gen-1 precursors, whose forge lottery
    produced four-figure negative percentages before this exclusion). Also
    excluded: a recipe that outputs the same item it consumes (T5-to-T6
    promotion is the family), because a route that hands the item back is
    not fodder demand for it.
    """
    best, best_source = None, None
    for recipe in graph.get("by_ingredient", {}).get(item_id, ()):
        if recipe["count_min"] != recipe["count_max"]:
            continue
        if recipe["output_id"] == item_id:
            continue
        if recipe["output_id"] in EXCLUDED_IDS:
            continue
        if any(i in CATALYST_IDS for i, _ in recipe["ingredients"]):
            continue
        out_bid = _bid(prices, recipe["output_id"])
        if not out_bid:
            continue
        revenue = out_bid * recipe["count_max"] * (1 - TAX)
        others, qty_used, priced = 0, 0, True
        for other_id, qty in recipe["ingredients"]:
            if other_id == item_id:
                qty_used += qty
                continue
            ask = _ask(prices, other_id)
            if not ask:
                priced = False
                break
            others += ask * qty
        if not priced or qty_used <= 0:
            continue
        # Clamped at zero, and a route that clears zero by nothing is not a
        # floor either, so it is dropped rather than published as 0 copper.
        value = max(0, int((revenue - others) / qty_used))
        if value <= 0:
            continue
        if best is None or value > best:
            best, best_source = value, recipe["source"]
    return best, best_source


def floor_coverage(floor, sell_price, tax=TAX):
    """Share of an item's liquidation mark that a structural floor underwrites.

    floor / (sell_price net of tax), so the two sides are comparable: vendor
    proceeds carry no trading post cut while a dump into the book does, and
    getting that asymmetry backwards understates every floor by 15%. None
    without a floor or a usable price; clamped at zero so a caller never has
    to render a negative percentage.
    """
    if floor is None or not sell_price:
        return None
    net = sell_price * (1 - tax)
    if net <= 0:
        return None
    return max(0.0, floor / net)


def structural_floor(item_id, row, prices, graph=None):
    """{"floor", "source", "coverage", "distance"} for one item.

    The best (highest) of the vendor floor and the conversion floor, with
    the provenance that produced it: "vendor", "conversion" or "none".
    coverage is the reported form (docs/INVESTING.md risk rule 7's floor
    distance is 1 - coverage, kept on the dict so both words resolve).
    `row` is a dw2 snapshot row; graph=None skips the conversion leg, which
    is what a caller with no archive parse should pass.

    Both reported forms are clamped into [0, 1] at their own end: coverage
    below zero is meaningless and distance below zero would render as a
    negative percentage on the one quantity risk rule 7 names as canonical.
    An item trading under its vendor buy-back (the scorers.flip data-glitch
    case) therefore reads as coverage 1-and-over, distance zero, which is
    "at floor" rather than "protected by 140% of itself".
    """
    floor, source = vendor_floor(row), "vendor"
    if graph:
        conv, conv_source = conversion_floor(item_id, prices, graph)
        if conv is not None and (floor is None or conv > floor):
            floor, source = conv, f"conversion {conv_source}".strip()
    if floor is None:
        source = "none"
    coverage = floor_coverage(floor, row.get("sell_price"))
    return {
        "floor": floor,
        "source": source,
        "coverage": round(coverage, 4) if coverage is not None else None,
        "distance": max(0.0, round(1 - coverage, 4)) if coverage is not None else None,
    }


# --- the ceiling ----------------------------------------------------------


def ceiling(item_id, prices, graph):
    """(copper, source) the cheapest alternative route to one unit, or
    (None, None).

    The minimum over every recipe that PRODUCES this item of its ingredient
    cost divided by the units it nets. Ingredients are bought at bid + 1,
    the same queue-front convention as invest.craft.cost_of; catalysts cost
    ZERO and a stochastic recipe yields its range MAXIMUM, both per the
    module docstring's catalyst rule, so the answer is the lowest defensible
    ceiling rather than the likeliest one.

    A recipe that also consumes the item it produces (promotion) nets
    count_max minus what it ate, and its own unit is left out of the cost:
    what a corner needs is the cost per additional unit acquired, not the
    cost of running the recipe.
    """
    best, best_source = None, None
    for recipe in graph.get("by_output", {}).get(item_id, ()):
        net_count = recipe["count_max"]
        cost, priced = 0, True
        for other_id, qty in recipe["ingredients"]:
            if other_id == item_id:
                net_count -= qty
                continue
            if other_id in CATALYST_IDS:
                continue
            bid = _bid(prices, other_id)
            if bid is None:
                priced = False
                break
            cost += (bid + 1) * qty
        if not priced or net_count <= 0:
            continue
        value = int(cost / net_count)
        if best is None or value < best:
            best, best_source = value, recipe["source"]
    return best, best_source


def ingredient_ids(item_ids, graph):
    """Sorted ids a caller must price to evaluate those items' ceilings.

    Catalysts are left out (they are priced at zero, never fetched), so are
    the archive's negative pseudo-ids, which encode currencies rather than
    items and have no trading post price at all, and so is the item itself
    on a promotion route (ceiling() nets it out instead of costing it). Same
    scoping idea as invest.craft.all_ids: fetch the small known set, not the
    whole table.
    """
    wanted = set()
    for item_id in item_ids:
        for recipe in graph.get("by_output", {}).get(item_id, ()):
            for other_id, _ in recipe["ingredients"]:
                if other_id <= 0 or other_id == item_id:
                    continue
                if other_id not in CATALYST_IDS:
                    wanted.add(other_id)
    return sorted(wanted)
