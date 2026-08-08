# Long-horizon investing

Design for the capital-scaling layer of the advisor: what to hold for weeks
to years, how to size it, and how to prove it beats the market. Companion to
[ROADMAP.md](ROADMAP.md); this document decides scope for milestone M6.

Numbers marked "live" were measured 2026-08-08 from the datawars2 snapshot
and the official exchange API.

## Why flips stop scaling

The flip scorer recycles at most `CAPITAL_PER_ITEM` (100g) across `TOP_N`
(50) picks on ~2-day round trips: about 5,000g working capital, and in
practice far less fills. Margins are 5-15%, so the strategy's income ceiling
is a few hundred gold per day no matter how rich the account gets. Every
gold above that ceiling idles at 0%.

What idling costs is regime-dependent, which is itself a finding. The gem
exchange (the only official gold-vs-outside-value gauge) shows strong gold
inflation for a decade (community reference: ~30g per 100 gems as the
pre-2024 norm) then two flat years: ~41g/100 in mid-2024, 38.4g/100 to buy
today, 25.7g/100 to sell (live). Meanwhile material prices moved hugely
both ways (mystic coins +160% in ten months, T6 mats halved over two
years). So "gold inflates, buy anything" is folklore; the real cost of
idle gold is the return of the strategies below plus whatever the market
index (built in M6.a) turns out to do. The job of the long book is to
earn that instead of assuming it.

## What the market can absorb (live anatomy)

- 27,977 marketable items; sell-side turnover ~1.05M gold/day (units bought
  off sell listings x price, 7d average).
- Extreme concentration: the top 50 items carry 50% of turnover, the top
  500 carry 84%. 268 items turn over more than 500g/day.
- Staples are enormous: Glob of Ectoplasm alone turns 164k g/day, Mystic
  Coin 40k g/day. Position sizes in the tens of thousands of gold are
  absorbable in days under our standard 25% capture cap.
- A real luxury tier exists: 117 items priced above 500g with nonzero
  weekly sales. Tradable gen-3 legendaries (~2,000g) move 3-6 units/day;
  gen-1 legendaries and precursors similar. Rare infusions and permanent
  contracts carry 25-40% bid-ask spreads at 1-5 sales/week.
- Festival goods hold huge dormant inventories off season (253k gold of
  candy corn listed in August, live) and their bags stay liquid year round
  (Trick-or-Treat Bags: 223k units/day even now).

That yields a capacity ladder. Each rung absorbs roughly an order of
magnitude more capital than the one before, at a longer holding period:

| rung | vehicle | working capital | cycle |
|---|---|---|---|
| 1 | daily flips (built) | up to ~5k g | days |
| 2 | festival cycles (built, undersized) | 10-30k g | months |
| 3 | luxury market making | 20-50k g | weeks |
| 4 | release basket + staples | 50-200k g | months |
| 5 | vault holds, corners | opportunistic | months-years |

## Ideas imported from finance

The market is a limit-order book traded by casual players, with a 15% sale
tax, no shorting, no derivatives, and all news published on a schedule
(patch notes, festival calendar, expansion cycle). That maps onto known
territory:

- Post-announcement drift. Retail markets underreact to public scheduled
  news; prices drift for days after announcements (Bernard-Thomas on
  earnings, the limited-attention literature). Patch notes are earnings
  calls. Being systematically early on published information is the
  cleanest edge available here.
- Theory of storage. Festival materials are seasonal commodities; whoever
  stores the glut earns the calendar spread, bounded by storage cost (zero
  here) and re-supply risk. The season module already measures these
  spreads; it just holds too little.
- Execution and price impact. Accumulating or exiting a large position in
  a thin book moves the price; spreading orders over time against measured
  daily flow (Almgren-Chriss style schedules, our capture cap) is the
  difference between a paper return and a real one.
- Corners and squeezes. Buying out a sell book is profitable only when
  re-supply is slow and demand at the higher price holds (the classic
  manipulation taxonomy). Both are measurable: daily `sell_listed` gives
  the refill rate, `sell_sold` the demand, and the recipe graph gives the
  price ceiling above which demand routes around the corner.
- Sizing and breadth. Fractional Kelly argues for sizing by edge over
  variance and never betting the bankroll on one thesis; the fundamental
  law of active management argues for many small independent bets over one
  big one. Both push the same way: caps per position, per strategy, per
  material class.
- Limits of arbitrage. The 15% tax and the absence of shorting are why
  mispricings persist here at all (Shleifer-Vishny): nobody can afford to
  correct them quickly. Two corollaries: overpricing is only ever a
  reason to not buy or to liquidate, never a trade; and convergence has
  no schedule, so positions must be sized to survive the mispricing
  widening first.
- Attention conditioning. Underreaction is larger when news competes for
  attention (the Friday-earnings and distraction literature). A balance
  tweak buried in an expansion launch drifts longer than a headline nerf
  everyone streams. Rank patch-affected items by salience and prefer the
  ignored tail; expect repricing of "old news" whenever a big creator
  rediscovers it.
- Dealer inventory. Flipping and the luxury desk are dealing (Ho-Stoll):
  quotes should skew against accumulated inventory (stop topping the buy
  book once holdings exceed a few days of fills, undercut harder to shed)
  instead of quoting symmetrically forever, and should widen in chaotic
  patch weeks when liquidity provision pays more.
- Benchmark discipline. "Ahead of the market" is a comparison, so the
  market needs an index. A turnover-weighted price index over the top-500
  items (84% of turnover) computed from the dw2 history is our CPI; every
  strategy is scored against it, inflation included, results stratified
  by liquidity tier (the CS2-skin ML literature's evaluation design).

The 15% tax sets the hurdle for everything: a hold breaks even only above a
17.6% gross rise (1/0.85). Any thesis that cannot plausibly clear ~25%
gross does not belong in the long book.

## The strategy book

Each strategy states its thesis, signal, sizing, exit, and what kills it.
All of them reuse the existing liquidity discipline: entry and exit sized by
capture share of measured flow, worst-case marked by dumping into the live
buy book (`features/book.py`), tax always included.

### S1. Release basket (the big-capital vehicle)

Thesis: major releases spike demand for the same classes: ectos, mystic
coins and clover inputs, T6 fine mats, lodestones, stabilizing matrices,
inscription/insignia inputs, and low-tier leveling mats (returning players
level alts; veterans do not farm starter zones). Releases are announced
weeks to months ahead, and each new legendary's material tree is public on
the wiki the moment it ships or gets datamined.

The catalyst calendar has changed shape (Aug 2026): there is no next
expansion. The VoE finale (~Sep 2026, one more legendary weapon plus an
armor set) is the last dated sink wave; after it comes a "foundations
year" and the Guild Wars 3 era, with the cross-game Hall of Monuments
rolling out era by era from late 2026. So the strategy generalizes from
"expansion cycle" to "dated sink waves": quarterly-release legendaries
and relic waves while VoE runs, then HoM phase announcements, each an
event window with a date and an affected material list.

Signal and evidence: an event study over the six expansion launches
already in our history (HoT through VoE) plus the quarterly drops: per
material class, the return from buying at launch-minus-90d and selling
into launch week. The season machinery (`season/cycles.py`) generalizes
from day-of-year windows to event-anchored windows; the evidence table
renders like the festival one (per-cycle returns, hit rate, worst cycle).

Sizing: this is the rung that absorbs 50k+ gold. Accumulate via laddered
buy orders over weeks, capped at 25% of daily flow; exit into the release
demand spike, never after it (new content also adds faucets, and
post-release farms crash the same mats). With the sink pipeline thinning
after September, sink-driven rallies are exited into their catalyst, not
held on momentum.

Kills: an announced faucet aimed at the class (wizard's vault material
boxes, convergence loot adding T6 supply); release delay (hold through,
thesis intact); buying after the announcement pop instead of before it;
multi-year holds that ignore the GW3 attention drain (fall 2027 betas)
on the far side of the calendar.

### S2. Patch-note theses (event registry + drift)

Thesis: balance previews, roadmap posts, and datamines are public days to
weeks before they hit; prices adjust slowly because participants are
casual. Examples of the pattern: rune/relic reworks repricing whole
categories, new legendaries repricing their inputs, weapon-master changes
moving sigils, and the recurring small version: every VoE quarterly drop
ships 6 crafted relics whose recipes pulse demand into rune-salvage
charms and symbols, on a published date.

Mechanism: automation cannot read patch notes, so the human stays in the
loop. A small thesis registry (YAML in the repo) records: item ids,
direction, the catalyst and its date, entry ceiling, target, invalidation
condition, size. The tool does everything else: sizes the entry against
book depth and flow, tracks the mark daily, nags when the catalyst passes,
scores the thesis in the paper tracker, and closes it out.

Support screens, all cheap:

- Abnormal-activity radar over all 27k items: z-scores of sold, listed,
  delisted, and price against a 90-day baseline, plus book-imbalance
  shifts from our 10-minute snapshots. It answers "what is moving that we
  have no thesis for" and surfaces accumulation by better informed
  traders before the news is legible. (Live example: Wintersday Gifts
  turning 1.29M units/day in August is exactly the kind of anomaly the
  radar exists to flag and a human to explain.)
- Pump filter, GE-Central style: price up more than ~4% on 4 of the last
  5 days in a low-float item is a retail pump pattern, not drift; with a
  15% tax the pumper's exit rarely works, so these are fade-or-avoid,
  never follow.
- Sink-anticipation watchlist: ArenaNet's economist posts describe the
  policy reaction function plainly: materials pinned at the vendor floor
  with huge fills eventually get a Mystic Forge sink or recipe aimed at
  them. Vendor-floor-pinned + high-flow is a computable screen for
  candidates that a future patch reprices upward; hold only tiny early
  positions (the catalyst has no date), upgrade to a thesis when a sink
  is announced.

Kills: acting on stale datamines, holding past a catalyst that fizzled
(the registry's invalidation date forces the exit decision), crowding into
plays already fully priced (entry ceiling).

### S3. Festival carry at size

Already built and validated by the cycle evidence; the change is capital.
`SEASON_CAPITAL` is a constant 50g per pick, which was right for proving
the pattern and wrong for using it. Replace with bankroll-driven
allocation (below); the big festival staples absorb thousands of gold
within the existing capture and exit-book caps. Multi-year holds are
allowed when the cycle return justifies them: the buy window is the
post-festival glut, not just the pre-festival ramp.

Two lessons from the twelve-year record temper this rung. The famous
carries got arbitraged: the ToT-bag pre-vs-post-Halloween spread has been
roughly zero (sometimes negative) since 2017, because bags are anchored
by their opening value and everyone knows the calendar. And only festival
items with year-round sinks hold value at all; purely decorative festival
mats sit at the vendor floor for years (snowflakes 2015-2017). So the
book trades what the per-cycle evidence actually shows, item by item,
rather than the folklore cycle: entries at the intra-festival glut low,
exits strictly before the next re-supply, and no position in anything
whose recent cycles have gone flat.

Kills: re-supply changes (a festival vendor starts selling the item, a new
source appears: the radar catches the flow change); one festival's meta
changing its currency sinks. The per-cycle evidence table already shows
regime breaks as bad recent cycles.

### S4. Luxury market making

Thesis: the 117-item luxury tier trades at 25-40% spreads with 1-5 sales a
week and almost no professional competition, because each unit ties up
thousands of gold and the flip crowd cannot size it. Buying at the bid via
patient orders and relisting at the ask nets 10-25% after tax per
round trip, at a pace of one or two round trips a month per item.

Mechanics: a dedicated collector pass fetches order books for the luxury
universe daily (about 120 ids, one or two API requests). Placement
follows the existing gap-walk logic; the new work is velocity estimation
from sparse books (fills per week from dw2 daily fills, cross-checked
against book diffs) and patience management (an order that sits 3 weeks
unfilled is repriced or withdrawn by rule, not mood).

Sizing: 1-3 units per item, 10-20 items concurrently, hard cap per item at
the exit-liquidity rule (below). Gen-3 legendaries are the entry point:
highest velocity in the tier and demand is structural (first-time
legendary buyers) rather than cosmetic fashion. Gen-1 precursors are
excluded outright: wizard's vault starter kits hand out free ones each
season and the price series has been policy-broken since 2023.

The tier has a hard roof: listings pin at a practical 10,000g cap (chak
egg sacs sat exactly there for years), and above it trade moves off the
trading post entirely, which this project does not touch. Items priced
near the pin have asks, not markets.

Kills: fashion collapse on a specific skin (diversify across items),
holding through a gem-store promo that substitutes for the item, marking
positions at ask instead of bid (self-deception; always mark at dump
value).

### S5. Vault holds (discontinued supply)

Thesis: items whose supply is fixed or shrinking (discontinued black lion
skins, retired infusions, permanent contracts, old festival exclusives)
against evergreen demand appreciate over years; they are the only asset
class here with a structural reason to beat inflation long term, because
the listed book plus unlisted hoards is the entire future supply. There
is also a structural bid under the class: the wallet cap (200,000g), the
guild-bank tab cap, and the 500g/week mail-transfer cap force whale
wealth into items, so the richest accounts hold their net worth in
exactly this shelf.

The twelve-year record ranks the shelf clearly. Permanent contracts are
the verified anchor: bank access went from ~300g (2013) to ~6,000g
(2026), roughly a decade of ~30%/year with 25-40% drawdowns (2026 is
one, cause unknown, which is an underwriting question, not a buy
signal). Truly never-rereleased skins did 30x over twelve years but on
books of five listings. Infusions split by faucet: lottery-drop ones
held (with 30-45% drawdowns from 2020 peaks), anything farmable by
organized groups decayed 90%+. And launch-week prestige items are always
the wrong price: every famous one fell 60-80% in its first year, so the
entry rule is "aged and proven scarce", never "new and shiny".

Signal: supply is observable (book depth trend, dw2 listed/delisted
churn), demand saturation is partially observable (gw2efficiency unlock
percentages for skins: a skin 2% of accounts own has room that a 40% one
does not). Book-shape convexity is screenable from our snapshots: a thin
ladder with steep gaps above the touch reprices violently on small demand.

Sizing: this is patient capital with weeks-long exits; cap the class at a
bankroll share and mark at dump value. Prefer items with a reprint-risk
story you can state (why ArenaNet is unlikely to re-issue it) and flag the
black lion re-release calendar as the event risk.

Kills: reprints. The statuette vendor structurally caps this class:
every chest run re-lists a rotating selection of old exclusives, so
"discontinued" is usually "dormant", and the appreciation curve resets
whenever an item cycles back. Chest editions rotate roughly every three
weeks and are datamined ahead of time (that_shaman's upcoming-features
posts), so the re-list risk is watchable. Beyond that: fashion drift, and
buying breadth-less items nobody actually wants (unlock stats and sale
velocity gate entry).

### S6. Corners and buyouts

The explicit "buying out stuff" play, and the one that most needs math
before gold moves. Buy the sell book up to price p, relist at p_r; profit
requires demand at p_r to outrun re-supply long enough to unload.

Screenable inputs, all already collected daily per item: demand rate
(`sell_sold`), refill rate (`sell_listed`), book value to swallow
(cumulative ask ladder from listings), and the structural ceiling: the
cheapest alternative route to the item (craft cost from the recipe graph,
forge conversion, vendor). A corner priced above the alternative-route
cost dies immediately; below it, the moat is real.

Corner score = days of demand the removed book represents, divided by days
of refill, bounded by ceiling headroom. Candidates are thin books (swallow
cost under a few hundred gold), steady collection or recipe demand, and
limited faucets (discontinued, festival-gated, rare drop). Position rule:
hard cap per corner (5-10% of bankroll), never corner anything actively
farmable at less than 2x current price, plan the unload over weeks at
capture-share of demand.

Two lessons from the commodity-corner literature carry over directly, and
GW2's own record confirms both. The binding constraint is the exit, not
the buyout: acquiring the book is easy, disposing of it without crashing
the price is the whole game, so the unload schedule is designed before
the first buy. And the exchange can change the rules mid-squeeze: the
2016-17 hardened leather squeeze (400x off its floor) ended when ArenaNet
shipped a dedicated leather farm, the way rule changes ended the Hunt
silver corner. Developer intervention is an explicit risk term, priced by
avoiding items whose oversupply or scarcity is visible enough to draw a
patch.

The domestic record also shows what works: the one well-documented
winning corner (Mini Kasmeer, 2015) combined a verified datamine
catalyst, a capped-supply item, an EV target computed before buying, and
a 36-hour exit. The failures were farmable items (the 2016 ToT-bag
accumulation died at festival re-supply) and buy walls mistaken for
floors: standing walls are one ban or one withdrawal away from vanishing
(the 2021 mystic coin wall dropped the price 22% overnight when its
owner was banned), so never treat another trader's wall as your exit
liquidity.

This is allowed play: ArenaNet has historically treated trading post
speculation and buyouts as part of the economy (no automation is involved;
all orders stay manual). It still deserves its own paper-track record
before real size.

### S7. Structural-bound arbitrage

Every material has computable bounds: a floor (vendor value, salvage
expectation, forge-fodder demand for precursor gambling) and a ceiling
(craft-from-inputs cost, mystic forge promotion cost, festival vendor
rates). The preserved gw2profits dump (8,062 recipes including forge
conversions with expected outputs) plus /v2/recipes gives us the whole
conversion graph offline.

Uses, in order of value:

1. Risk metric: floor distance ((price - floor)/price) per held position;
   the closest thing to a hedge this market offers is holding assets that
   sit on structural floors.
2. Corner ceiling (S6 above).
3. Mispricing screen: items trading above their craft ceiling (sell the
   inputs' conversion) or below their salvage floor (buy and salvage);
   T5-to-T6 promotion spreads; unidentified gear price vs its salvage
   expectation, which is a large liquid market (rare unid gear: ~12.7k
   g/day turnover, live).

### S8. Time-gated crafting carry

Daily-cooldown refinements (mithrillium, elder spirit residue, silk
weaving thread, elonian cord, charged quartz) are supply rationed per
account per day, so their spread over inputs is a persistent yield: buy
inputs, craft daily, sell weekly. Low risk, low effort, a few gold per day
per account: the "dividend" baseline for idle capital, and the first thing
capital does while waiting for entries elsewhere. Track the spread series
per refinement so dead carries (spread below tax) drop off the list.

### S9. Long-horizon reversal with a break guard

The STL machinery already separates trend, season, and residual. Items
sitting far below trend (residual z < -2 over 90d) with stable flow revert
more often than not, unless the level shift was a regime change. The guard
is a change-point test (CUSUM on price and flow): a detected break means
"new faucet or dead demand, do not catch the knife"; no break plus intact
flow means "overshoot, accumulate". Exit at residual zero or a time stop.
This extends the flip philosophy to the 1-3 month horizon and recycles the
same evidence-first display: how often did reversion complete, how long
did it take, worst case.

### S10. New-release lifecycle cohorts

New items follow a repeatable arc: launch spike, farm-driven crash to the
floor while the content is popular, then slow recovery as farmers rotate
to the next release, sometimes a dead-content premium years later. With
release dates per item (dw2's `first_added`, cross-checked against
gw2treasures first-discovery timestamps) the whole history collapses into
price-vs-age curves per cohort; the systematic entry is the
farm-peak trough, which lands roughly when the next release is announced,
and the cadence is yearly and public. (Live example of the pattern's
input: Unstable Kryptis Motivations and Aetheric Anchors both sit in the
top-20 turnover a year into their lifecycles.)

### Tracking only: the gem exchange

Gold-to-gems round trips lose ~35% to the implicit spread, so the rate is
not tradable, but it is the inflation index and occasionally a
consumption signal (gem sales dip the rate; buying utility gems on dips is
real savings, just not portfolio return). Collect the rate daily (two API
requests) and chart it on the report; use it as one of the benchmark
lines.

## Risk framework

The dominant risk has a name: policy. ArenaNet moves faucets and sinks,
sometimes without notice, and the casualty list is long: precursors -80%
in two years of wizard's vault starter kits, mystic coins -55% when EoD
cut per-legendary coin demand, unidentified dye -95% after re-release,
silk and hardened leather each round-tripping 10-400x runs. Every hold is
implicitly short a patch. That is why the framework below leans on class
caps, catalysts inside twelve months, and tripwires rather than
conviction.

Hedging here cannot mean offsetting positions: no shorts, no derivatives.
What it can mean, concretely:

1. Bankroll config: one number the user maintains (investable gold), plus
   allocation caps per strategy rung, e.g. flips uncapped (self-limiting),
   festival 25%, luxury 25%, release basket 30%, vault 15%, corners 10%
   hard. Caps bind at entry time; the report shows current exposure vs cap.
2. Exit-time sizing: every position's size obeys
   `qty <= capture x sell_flow x max_exit_days` for its tier (flips 2d,
   festival in-window 14d, luxury 30d, vault/corners 90d), and the dump
   loss floor from the live book, reusing `apply_exit_floor`. A position
   you cannot leave is not an investment, it is furniture.
3. Class exposure: items tagged by material class (T6, festival, luxury,
   staples, new-release); one patch tends to hit one class (a magic-find
   change reprices all fine mats at once), so class caps are the real
   diversification unit, not item counts.
4. Event-risk calendar: the release table already exists; positions in
   farmable materials get flagged when a launch approaches (new faucets),
   vault positions get flagged around black lion rotation announcements.
5. Tripwires instead of stops: daily change-point monitoring on price and
   flow per held item (CUSUM to start; the Bayesian online variant later,
   with the patch calendar as the hazard prior, so known patch dates
   raise the break probability instead of surprising it). A detected
   break fires a re-underwrite task in the action queue (the thesis
   registry's invalidation field decides sell vs hold), because in a
   gap-prone book a mechanical stop just donates the spread.
6. Conservative marks: NAV always values positions at dump-into-bid net of
   tax, never at ask. Optimism lives in theses, never in accounting.
7. Structural floors: prefer positions with computable floors (S7); the
   floor distance is reported per position and in aggregate.

## Benchmarks and scorekeeping

- Market index: turnover-weighted mid-price index over the trailing top
  500 items, chained monthly (new items enter, dead ones leave), computed
  from the dw2 history back to 2013. This is the "the market" in "beat the
  market"; it doubles as the inflation series.
- Benchmark lines on the report: index, raw gold (flat), ecto, gem rate.
- Portfolio NAV: daily series from account holdings (API) valued at dump
  net; plotted against the benchmarks over 30/90/365-day windows.
- Paper track per strategy: every recommendation the long book emits opens
  a paper position exactly like the flip tracker does today, with the
  strategy tag carried through, so each strategy accumulates its own hit
  rate, realized-vs-promised return, and time-to-exit record. Strategies
  that underperform the index over a full cycle get demoted to
  tracking-only. The same infrastructure doubles as the personal
  scoreboard once positions are real (account transactions already flow
  in via `account/`).

## Data plan (fits the zero-budget design)

New collection, all within existing free tiers (sources verified live
2026-08-08; details and links in [RESEARCH.md](RESEARCH.md)):

| what | source | cadence | cost |
|---|---|---|---|
| luxury + corner + held books | /v2/commerce/listings | daily | 2-4 req |
| gem rate spot | /v2/commerce/exchange | daily | 2 req |
| gem rate history to 2013 | gw2tp /api/gems | once | 1 req |
| abnormal-activity baselines | existing dw2 snapshot | daily | 0 |
| demand map: recipes consuming X, achievement bits, wizard's vault shop | official API + wiki SMW `Has ingredient` | weekly | ~20 req |
| supply map: container drop tables, vendor sinks, discontinued flags | gw2treasures API + wiki SMW | weekly | few req |
| item release dates (lifecycle cohorts) | dw2 `first_added` | free | 0 |
| unlock saturation (skins/minis) | gw2efficiency /tracking/unlocks | weekly | 1 req |
| recipe/conversion graph incl. forge | archive + /v2/recipes + dw2 recipes | static | 0 |
| BL chest + gem-store promo calendar | dw2 gemstore catalogue RSS + wiki BLC/historical | weekly | 1-2 req |

Storage: order books for ~300 tracked ids at one snapshot/day is a few MB
per month; index series and baselines are kilobytes. Nothing threatens the
25GB cap. Collector hardening worth knowing: bulk /v2/commerce/prices
silently drops ids that are currently unsellable, so absence from a
response is not evidence an item died.

## Milestones

- M6.a foundations: bankroll config, strategy/class tags, exposure caps,
  the market index, benchmark lines on the report, NAV vs index. Nothing
  trades differently yet; we can just see.
- M6.b festival at size + carry list: season sizing from bankroll,
  time-gated craft spread table. Lowest-risk capital deployment first.
- M6.c luxury desk: universe definition, daily book collection, velocity
  estimates, entry/exit/patience rules, paper track. First new alpha.
- M6.d event layer: thesis registry, abnormal-activity radar, release
  event study, release calendar risk flags. The "ahead of the market"
  layer.
- M6.e opportunistic: corner screen with ceiling math, structural-bound
  mispricing screen, reversal-with-guard, lifecycle cohorts. Each ships
  with paper tracking before size.

Acceptance for the whole milestone: six months of NAV and paper records
per strategy against the index, with at least the luxury desk and festival
book showing realized index-beating returns after tax at their actual
sizes.

## What we deliberately skip

- Automation of in-game actions: everything stays manual; the tool
  advises. (Compliance stance unchanged.)
- Gold-to-gem-to-gold trading: the spread makes it strictly consumption.
- Cross-account operations, alt armies for time-gated crafts, anything
  that smells like ToS risk.
- Sentiment scraping (reddit/YouTube NLP): the radar detects the price
  effect; a human reads the cause. Revisit only if the radar proves
  blind.
- Real-money anything.

## Sources

[RESEARCH.md](RESEARCH.md) carries the evidence base this design draws on:
the reading list (virtual-economy studies, finance frameworks, tooling
designs), the verified data and tooling inventory, the August 2026
current-cycle notes with the dated positioning read, and the community
playbook with fourteen years of case studies. This document stays
decision-only.
