"""Material-class tags for holdings and long-book positions.

One patch tends to reprice one class at a time (a magic-find change hits
every T6 fine mat at once), so class exposure is the diversification unit
the risk framework in docs/INVESTING.md caps. Explicit id sets cover the
classes a patch targets by name; the luxury class is a price rule, since
"expensive and slow" is a property, not a fixed list.

Ids were resolved from the live datawars2 snapshot on 2026-08-08; names in
the comments are for the reader, the ids are what counts.
"""

# The eight T6 fine crafting materials, one patch away from repricing
# together (magic find, salvage rates, a new legendary's gift tree).
T6 = frozenset({
    24295,  # Vial of Powerful Blood
    24283,  # Powerful Venom Sac
    24300,  # Elaborate Totem
    24277,  # Pile of Crystalline Dust
    24351,  # Vicious Claw
    24357,  # Vicious Fang
    24289,  # Armored Scale
    24358,  # Ancient Bone
})

# Liquid megacaps every release-demand wave routes through.
STAPLES = frozenset({
    19721,  # Glob of Ectoplasm
    19976,  # Mystic Coin
    68063,  # Amalgamated Gemstone
    89103,  # Charm of Brilliance
    89258,  # Charm of Potence
    89216,  # Charm of Skill
    89141,  # Symbol of Enhancement
    89182,  # Symbol of Pain
    89098,  # Symbol of Control
})

# Festival materials and bags: supply pulses once a year, so the whole
# class shares re-supply risk on the festival calendar.
FESTIVAL = frozenset({
    36038,  # Trick-or-Treat Bag
    36041,  # Piece of Candy Corn
    47909,  # Candy Corn Cob
    36060,  # Chattering Skull
    36061,  # Nougat Center
    77604,  # Wintersday Gift
    99293,  # Jolly Wintersday Gift
    92586,  # Rimed Verdant Wintersday Gift
    86601,  # Snowflake
    38130,  # Tiny Snowflake
    38131,  # Delicate Snowflake
    38132,  # Glittering Snowflake
    38133,  # Unique Snowflake
    38134,  # Pristine Snowflake
    38135,  # Flawless Snowflake
    38143,  # Exquisite Snowflake
    43319,  # Piece of Zhaitaffy
    43320,  # Jorbreaker
    68646,  # Divine Lucky Envelope
})

# Anything this expensive trades in single units with weeks-long exits,
# whatever it is; the luxury desk (M6.c) and the vault shelf live here.
LUXURY_MIN_PRICE = 500 * 10_000  # copper

CLASSES = ("t6", "festival", "staples", "luxury", "other")

_EXPLICIT = {}
for _id in T6:
    _EXPLICIT[_id] = "t6"
for _id in FESTIVAL:
    _EXPLICIT[_id] = "festival"
for _id in STAPLES:
    _EXPLICIT[_id] = "staples"


def classify(item_id, sell_price=None):
    """Class name for an item; explicit sets win over the luxury price rule."""
    cls = _EXPLICIT.get(item_id)
    if cls:
        return cls
    if sell_price and sell_price >= LUXURY_MIN_PRICE:
        return "luxury"
    return "other"
