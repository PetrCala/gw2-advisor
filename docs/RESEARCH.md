# Research notes for the long-horizon book

Supporting material for [INVESTING.md](INVESTING.md): the reading list, the
data/tooling inventory, and current-cycle notes. Compiled 2026-08-08;
links were live then. Facts here inform the design but decisions live in
INVESTING.md.

## Reading list

What each item contributes to this project, grouped by theme. All links
verified at compile time unless marked otherwise.

### Virtual-economy studies

- Castronova 2001, "Virtual Worlds" ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=294828)).
  The founding measurement of a game economy; the citation that price
  theory applies in-game.
- Lehdonvirta and Castronova 2014, "Virtual Economies" (MIT Press). The
  designer-side view of sinks, faucets, and intervention; ArenaNet is the
  counterparty this book describes.
- CCP's EVE Online monthly economic reports
  ([example](https://www.eveonline.com/news/view/monthly-economic-report-july-2026)).
  The template for macro accounting: money supply, faucet/sink balance,
  price indices. Our market index and gold-inflation series copy this.
- Belaza et al. 2020, EVE spreads and volumes vs real-world conditions
  ([PLOS ONE](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0240196)).
  Peer-reviewed evidence that game-market spreads and volumes carry
  behavioral signal.
- Drachen et al. 2016, the complete Glitch auction-house dataset
  ([arXiv](https://arxiv.org/abs/1603.07610)). Template for cohort and
  lifecycle analysis of fills.
- Scholten et al. 2019, statistics of 3,467 OSRS Grand Exchange series
  ([arXiv](https://arxiv.org/abs/1905.06721)). The closest published
  analog to our dataset shape; same stationarity discipline applies.
- Varoufakis 2012, TF2 arbitrage essay
  ([mirror](https://gwern.net/doc/economics/2012-varoufakis-teamfortress2arbitrage.html)).
  Retail game markets close arbitrage slowly; the one-page thesis of this
  whole project.
- CS2 skin-market ML: Guede-Fernandez et al. 2025
  ([Frontiers](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1702924/full))
  and "Skin in the game" 2025
  ([FRL](https://www.sciencedirect.com/science/article/pii/S1544612325009298)).
  Fee-aware per-item forecasting on 12k items with a 10% fee and holding
  locks; the evaluation design (net-of-fee portfolio vs buy-and-hold,
  stratified by liquidity tier) is the one our tracker adopts.
- Diablo 3 RMAH post-mortem
  ([Shokrizade](https://raminshokrizade.substack.com/p/9p-smedleys-dream-part-i-and-ii-effects),
  [BBC](https://www.bbc.com/news/technology-24152225)). Farmable supply
  destroys scarcity value at brutal rates; the null hypothesis for every
  scarcity thesis.
- John Smith (ArenaNet economist), official economy posts
  ([virtual economy](https://www.guildwars2.com/en/news/john-smith-on-the-guild-wars-2-virtual-economy/),
  [disequilibrium](https://www.guildwars2.com/en/news/economy-report-brace-yourself-disequilibrium-is-coming/)).
  The policy reaction function in the developer's own words: oversupplied
  materials get forge sinks. Feeds the sink-anticipation watchlist.
- OSRS merch-clan documentation
  ([RS wiki editorial](https://runescape.wiki/w/RuneScape:Wiki_Post/Editorials/GE_Merchanting),
  [GE Central clan tracker](http://www.grandexchangecentral.com/list.php?list=clan)).
  Field guide to retail pumps and the cheap persistence screen (price up
  more than 4% on 4 of 5 days) our radar reuses as a pump filter.

### Practitioner tooling

- TradeSkillMaster market value and sale rates
  ([algorithm](https://support.tradeskillmaster.com/en_US/custom-strings/how-is-auctiondb-market-value-calculated),
  [sniper](https://blog.tradeskillmaster.com/tsm4-deep-dive-sniper/)).
  Outlier-trimmed fair value (drop listings past a 20% jump, blend a
  14-day decayed average) resistant to troll listings; our luxury marks
  copy this. Our fill counts are a better sale-rate stat than TSM had.
- OSRS wiki real-time prices
  ([project](https://oldschool.runescape.wiki/w/RuneScape:Real-time_Prices),
  [FAQs](https://prices.runescape.wiki/osrs/faqs)). Honest documentation
  of which side of the book each price comes from; most GW2 signal bugs
  come from mixing sides, so their discipline is worth copying.
- Orbital Enterprises, "EVE Market Strategies"
  ([book](https://orbitalenterprises.github.io/eve-market-strategies/index)).
  Full quant treatment of a game order book with worked backtests;
  station trading is our flip engine with different constants.
- VertoxQuant, forecasting RuneScape prices
  ([post](https://www.vertoxquant.com/p/forecasting-runescape-prices)).
  Per-item AR(1) with fees as the cheapest first forecasting experiment;
  in our tax regime it times entries inside trades rather than standing
  alone.
- SimonPop, WowAuctionForecaster
  ([repo](https://github.com/SimonPop/WowAuctionForecaster)). Crafting
  graph as a price-forecasting structure; even without the GNN, the
  crafting spread over the recipe graph is a mean-reversion feature.
- poe.ninja ([site](https://poe.ninja)) and poe-antiquary
  ([site](https://poe-antiquary.xyz/)). Patch-aware price-history
  presentation (series segmented by league/era, not calendar); the right
  display idiom for a patch-driven economy.

### Finance frameworks

- Jegadeesh-Titman 1993 momentum
  ([PDF](https://www.bauer.uh.edu/rsusmel/phd/jegadeesh-titman93.pdf)):
  4-12 week winners persist; long-only monthly momentum is viable here
  because the tax forces long horizons anyway.
- De Bondt-Thaler 1985 long-horizon reversal
  ([Wiley](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1985.tb05004.x)):
  the post-nerf capitulation trade.
- Bernard-Thomas 1989 post-announcement drift
  ([JSTOR](https://www.jstor.org/stable/2491062)): patch notes as
  earnings; buy the pop, ride the diffusion.
- DellaVigna-Pollet 2009
  ([PDF](https://eml.berkeley.edu/~sdellavi/wp/earnfr080204.pdf)) and
  Hirshleifer-Lim-Teoh 2009
  ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=980958)):
  drift grows when attention is elsewhere; size drift trades by how
  buried the news was.
- Huberman-Regev 2001
  ([Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00330)):
  prices move on attention, not information; a creator rediscovering old
  news is a tradable event.
- Working 1949
  ([PDF](https://news.fbc.keio.ac.jp/~hayami/pdf/finance/futures/Working1949.pdf))
  and Fama-French 1987 on storage: festival mats are seasonal
  commodities; buy when the calendar discount exceeds carry.
- Kyle 1985
  ([PDF](https://personal.utdallas.edu/~nina.baranchuk/Fin7310/papers/Kyle1985.pdf)):
  price impact linear in flow; per-item lambda from our 10-minute
  snapshots is the capacity model.
- Almgren-Chriss 2000
  ([PDF](https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf)):
  execution schedules for accumulating and unwinding in thin books.
- Amihud 2002 ILLIQ
  ([PDF](https://www.cis.upenn.edu/~mkearns/finread/amihud.pdf)):
  |return|/fills as the one-line liquidity screen; Nagel 2012
  ([NBER](https://www.nber.org/papers/w17653)): liquidity provision pays
  most in chaotic weeks, so quote wider then.
- Shleifer-Vishny 1997 limits of arbitrage
  ([PDF](https://web.stanford.edu/~piazzesi/Reading/ShleiferVishny1997.pdf)):
  why our edges exist and why overpricing persists untradeably.
- Allen-Gale 1992 manipulation taxonomy
  ([OUP](https://academic.oup.com/rfs/article-abstract/5/3/503/1576822))
  and Pirrong 2017 corners survey
  ([PDF](https://www.bauer.uh.edu/spirrong/manipulation_review_JCM_Pirrong.pdf)):
  corners profit only with inelastic re-supply and a planned exit;
  rule changes (patches) end squeezes, as with Hunt silver.
- Kelly 1956
  ([PDF](https://www.princeton.edu/~wbialek/rome/refs/kelly_56.pdf)) and
  MacLean-Thorp-Ziemba
  ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1797366)):
  fractional Kelly on tracker-estimated edges, low fractions because
  regimes break on patch days.
- Grossman-Zhou 1993 drawdown control
  ([Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-9965.1993.tb00044.x)):
  scale open risk to the cushion above a bankroll floor.
- Grinold 1989 fundamental law
  ([PM Research](https://www.pm-research.com/content/iijpormgmt/15/3/30)):
  breadth beats conviction, but the tax cuts realized breadth; compute a
  cost-adjusted information ratio.
- Ho-Stoll 1981
  ([ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/0304405X81900209))
  and Avellaneda-Stoikov 2008
  ([PDF](https://people.orie.cornell.edu/sfs33/LimitOrderBook.pdf)):
  inventory-skewed quoting; directly implementable for the flip and
  luxury desks with the tax as the spread floor.

### Change-point detection

- Page 1954 CUSUM
  ([OUP](https://academic.oup.com/biomet/article-abstract/41/1-2/100/456627)):
  the nightly O(1) tripwire on fills and price per item.
- Adams-MacKay 2007 Bayesian online change-point
  ([arXiv](https://arxiv.org/abs/0710.3742)): run-length posterior with
  the patch calendar as the hazard prior.
- Killick et al. 2012 PELT ([arXiv](https://arxiv.org/abs/1101.1438)) and
  Truong et al. 2020 with the ruptures library
  ([arXiv](https://arxiv.org/abs/1801.00718),
  [GitHub](https://github.com/deepcharles/ruptures)): offline regime
  labeling of the full history, used to measure how long post-patch
  drift actually lasts and to set holding periods.

Gaps the search confirmed: no peer-reviewed work on WoW auction sniping
bots or Path of Exile currency markets; practitioner sources above are the
substitutes.

## Data and tooling inventory

All entries verified live on 2026-08-08 unless noted.

### Official API (api.guildwars2.com/v2)

- Commerce: `prices` (best bid/ask + queue sizes), `listings` (full
  consolidated books), `exchange/coins` and `exchange/gems` (spot only,
  no history), `transactions/{current,history}/{buys,sells}` (auth; 90-day
  history, 5-minute cache), `delivery` (auth). No commerce schema changes
  2024-2026 ([changelog](https://wiki.guildwars2.com/wiki/API:Changelog)).
- Limits: token bucket, 300 burst refilling 5/sec; bulk requests cap at
  200 ids ([best practices](https://wiki.guildwars2.com/wiki/API:Best_practices)).
- Quirks: bulk `prices` silently drops currently-unsellable ids (absence
  is not death); `/v2/achievements/daily` is permanently dead since the
  wizard's vault.
- Demand mapping: `/v2/achievements` `bits` (Item/Skin/Minipet) and
  `type: ItemSet` are the item-to-collection join; `recipes/search?input=X`
  lists recipes consuming X; `/v2/wizardsvault/listings` exposes the
  rotating astral-acclaim shop (the mystic coin/clover faucet caps).
- Reliability: 2025 saw an EU-to-NA migration bug breaking some accounts'
  authed endpoints, a 3-day October API outage, and a December TP outage;
  [status.gw2efficiency.com](https://status.gw2efficiency.com/) monitors
  every endpoint live. The collector's jitter tolerance already fits.

### datawars2.ie (active; GitLab pushes through 2026)

- Snapshot `/gw2/v1/items/json`: 184 fields including 1d/7d/1m/3m/12m
  sold/listed/delisted aggregates, `first_added` (release-date source),
  `marketable`, `craftable`
  ([endpoint docs](https://gitlab.com/Silvers_Gw2/Market_Data_Processer/-/wikis/endpoints)).
- Daily history `/gw2/v2/history/{json,csv}` back to 2012-10 (early years
  price extremes only; flow fields begin ~2019); hourly history retains
  about a week; `/gw2/v2/history/total` aggregates the whole market.
- `/gw2/v2/recipes`: official recipes plus community mystic forge.
- `/gw2/v1/gemstore/catalogue/{json,feed/rss}`: rolling gem-store
  catalogue with availability windows; the programmatic BL promo
  calendar.
- Sampling ~5 minutes (inferred from `count: 287` per day).

### gw2tp.com (revived April 2026)

- `/api/trends-ohlc?id=N&range=all`: per-item daily OHLC + supply/demand
  back to 2012-09, the deepest fetchable per-item series anywhere.
- `/api/gems?range=all`: daily gem-rate candles (coins per 100 gems)
  back to 2013-09; the sell direction only since 2026-04.
- `/api/game-updates`: release annotations. Caveat: Cloudflare JS
  challenge; endpoints work from a browser context. The old open-source
  `api.gw2tp.com` bulk CSV host is dead.

### gw2treasures.com (documented API, free key, active repo)

- `api.gw2treasures.com`: `items/bulk/tp-prices` (1000 ids per request,
  five times the official cap), `items/bulk/container-contents` (typed
  drop tables: the faucet map), `items/:id/mystic-forge`, achievements;
  `X-Created-At` gives first-discovery timestamps
  ([repo](https://github.com/GW2Treasures/gw2treasures.com)).

### gw2efficiency.com (undocumented but stable)

- `api.gw2efficiency.com/tracking/unlocks?id=skins|minis|gliders|outfits`:
  unlock counts over a ~486k-account sample, no auth; divide by `total`
  for saturation. The S5 demand-saturation gate. Playerbase statistics
  endpoints exist; no public price-history endpoint.
- [custom-recipes repo](https://github.com/gw2efficiency/custom-recipes):
  community mystic-forge recipe dataset.

### Official wiki (MediaWiki + Semantic MediaWiki)

Verified `action=ask` queries through `api.php` (`api_version=3`,
`|limit=500`):

- `[[Has ingredient::Mystic Coin]]`: recipes consuming an item, mystic
  forge included (hand-curated, so it covers what the official API
  misses).
- `[[Sells item::X]]`: vendor rows including wizard's vault and
  historical vendors (sink map).
- `[[Has availability::Discontinued]]`: the discontinued-item universe
  for S5/S6 screens.
- `Contains item`, `Dropped by`, `Gathered from` properties exist for
  source mapping. Achievement requirements are NOT in SMW; use official
  API bits.
- Structured pages worth parsing: `Gem Store/data` and
  `Gem Store/data (historical)` (availability windows),
  `Black Lion Chest/historical` (dated rotation contents back to 2012).

### Other

- gw2bltc.com: undocumented `api/tp/chart/{id}`, 6-hour resolution,
  ~5 years depth, with sold/bought velocity columns (positions inferred).
- fast.farming-community.eu: farming benchmarks now login-gated behind
  bot protection (Aug 2026); no public API.
- gw2lunchbox.com: statuette-EV and shipment calculators, no API.
- New entrants 2024-2026: AuricDB (multi-game market tracker, 5-minute
  cadence), gw2trader.gg (market-cap analytics), gw2lax.com, drf.rs
  (drop-rate telemetry addon; potential faucet quantification source).
- Dead: gw2spidy, api.silveress.ie, old api.gw2tp.com, and
  gw2profits.com goes dark 2026-08-15 (its recipe/salvage dump is
  preserved in this repo's `archive/`).

## Current cycle notes (August 2026)

Dated snapshot; rots fast, re-verify before acting. Sources are the
official news blog, the wiki, and price pulls from gw2bltc/gw2pc on
2026-08-08.

### Roadmap

- Current expansion: Visions of Eternity (6th), launched 2025-10-28.
  Quarterly drops so far: Raids and Wardrobe (2026-02-03: raid encounter,
  reward unification, 6 relics, legendary ring Endless Summer), The Only
  Way (2026-05-12: story, map, 6 relics, legendary accessories Stella
  Radians and Strife Unending)
  ([wiki](https://wiki.guildwars2.com/wiki/Guild_Wars_2:_Visions_of_Eternity)).
- VoE finale ~Sep 1, 2026 (wiki-listed, not officially dated): story
  conclusion, 4th map, raid legendary mode, new fractal and convergence,
  6 relics, one new legendary weapon (TBA) and a new armor set
  ([franchise post](https://www.guildwars2.com/en-gb/news/future-of-the-guild-wars-franchise/)).
- No 7th expansion. Guild Wars 3 announced 2026-06-05 (betas planned fall
  2027); after the finale GW2 gets a "foundations year" (QoL passes, WvW
  borderlands beta, Zhaitan rework) plus a phased cross-game Hall of
  Monuments starting late 2026, rolled out era by era
  ([GW3 post](https://www.guildwars2.com/en/news/celebrating-the-announcement-of-guild-wars-3/)).
- Festivals: Four Winds runs Aug 11 - Sep 1, 2026 (live during this
  note); Halloween/Wintersday 2026 dates unannounced; no 2027 dates
  anywhere yet. Festivals start and end on Tuesdays.

### Faucet and sink state

- Wizard's vault (quarterly seasons; next rollover ~Sep 1): mystic coins
  9 AA capped 60/season, clovers 60 AA capped 20/season, the unlimited
  1g coin bag retired Feb 2026 (gold faucet cut), unlimited crafting
  material box added (material faucet), BL keys 450 AA
  ([wiki](https://wiki.guildwars2.com/wiki/Wizard's_Vault)).
- Homesteads (since Aug 2024) put a standing weekly bid under junk mats:
  refiners eat ores, logs, and cooking mats (peppercorn, flax, omnomberry
  and friends) at 200-800 trades/account/week
  ([refinement lists](https://en.gw2treasures.com/homestead/materials)).
  Structural floor change for low-tier mats.
- Feb 2026 raid-reward unification (magnetite as the single currency,
  800/week cap, vendorable exotics/cores) is mildly deflationary for
  those markets.
- Every VoE drop adds 6 crafted relics consuming rune-salvage charms and
  symbols; next pulse lands with the finale.

### Market state (price pulls 2026-08-08)

- Mystic Coin: ~1.9-2.1g, up ~160% since VoE launch (was pinned at
  71-80s through the whole wizard's-vault era, 2023-2025). The
  clover-hungry VoE legendaries (Aetheric Anchor consumes 100 clovers;
  three legendary trinkets since) against the capped WV faucet repriced
  it back to 2021 levels. The run already happened.
- Glob of Ectoplasm: 28.5s, +90% off the early-2025 floor (15-16s).
  Same driver; Aetheric Anchor has no precursor tree, so ecto demand is
  direct.
- T6 fine mats: roughly halved from 2022 (blood 24.7s, bones 27.9s,
  dust 18s; full 250-stack set ~430g). Community attributes the glut to
  rift/convergence loot and WV material boxes. Multi-year cheap.
- Gem rate: 154g buys 400 gems; selling 400 gems yields ~103g. Flat to
  slightly down since mid-2024 (~164g/400 then); strong inflation is a
  pre-2024 story. Wallet gold cap still 200,000g.
- Population/revenue: Steam concurrents peaked at an all-time 10.8k at
  VoE launch (Nov 2025); Q4 2025 and Q1 2026 were GW2's best revenue
  quarters since 2017 ([NCSoft via massivelyop](https://massivelyop.com/2026/05/14/ncsoft-q1-2026/)).
  The game enters the GW3 era healthy.

### Positioning read (as of 2026-08-08, expires with the finale)

1. T6 fine mats are the asymmetric entry: multi-year cheap, with a dated
   demand catalyst (finale legendary weapon + armor set, ~Sep 1) about
   three weeks out. Risk: the finale's convergence adds T6-bearing loot;
   size within exit-book caps and exit into the catalyst week.
2. Charms and symbols get their recurring 6-relic pulse at the finale;
   small, repeatable, and datamined in advance (that_shaman posts the
   recipes early).
3. Mystic coins and ectos carry the same catalyst but the entries are
   late (+160%/+90% already); after the finale the sink pipeline goes
   quiet for at least a year, so these are exit-into-strength candidates
   for existing holders, not fresh buys at size.
4. Post-finale regime: no dated sink waves until Hall of Monuments
   phases announce. Each HoM phase (core Tyria first, then per
   expansion) is a potential demand catalyst for that era's collection
   and legendary materials; watch the announcements and datamines, enter
   via the thesis registry.
5. Festival of the Four Winds opens Aug 11 (the season queue handles its
   goods); the near-certain late-August anniversary gem sale makes the
   gem rate volatile in both directions, so time any planned utility gem
   purchases around it rather than trading it.
6. Long-horizon macro risk: GW3 betas (fall 2027) start pulling
   attention; multi-year GW2 holds now carry migration risk that did not
   exist before June 2026. Prefer catalysts inside the next 12 months.
7. Permanent contracts (the decade-long ~30%/yr class anchor, see the
   playbook below) are in an unexplained 2026 drawdown: bank access
   ~6,000g against an 8,500g 2025 high. If the vault module's first
   underwriting pass finds sale velocity intact, the drawdown is the
   entry; if flow died with the GW3 announcement, it is the regime
   change. Evidence first, then size.

## Community playbook and case studies

Fourteen years of long-hold outcomes, from datawars2 daily history pulls
(2026-08-08) plus recovered forum/reddit threads. The condensed verdicts;
each shaped a rule in INVESTING.md.

### What the multi-year record says per asset class

- Mystic Coin: 3.3s (2012), 73c low (2014), ~1g (2017), ~2g (2021), 80s
  crash (post-EoD 2022), ~2g again (2026). The 2015-2021 run was a
  one-time repricing (faucet cut + gen-2 legendary demand); since 2021 it
  is a 0.8-2.5g range asset with policy risk. Buying faucet-scare panics
  worked; buy-and-hold from 2g returned nothing in five years.
- Ectoplasm: trades in 2026 where it traded in 2013 (~25s), inside a
  10-56s band. Near-cash with a coupon: park value, sell into crafting
  spikes, expect no drift.
- T6 fine mats: spike into legendary-demand events (2016 peak never
  revisited; brief SotO armor pop 2024), decay between them. Buy dull
  periods, sell announcement/launch weeks; holding through a cycle
  round-trips.
- ToT bags: the founding 13x carry (19c to 2.63s, 2012-13) is gone; the
  pre-vs-post-Halloween spread has been ~zero since 2017 because bag
  price is anchored by opening EV
  ([mass openings](https://reddit.com/r/Guildwars2/comments/9rkdu3/data_current_value_from_100_000_trickortreat_bags/)).
  Candy corn did 50x (2012-14) then -86% in three months when the next
  festival re-flooded it.
- Snowflakes: 3 straight years at the 2c vendor floor (2015-17) until
  recipes gave them a sink. Festival mats without year-round sinks go to
  the floor and stay.
- Discontinued BL skins: Ghastly Grinning Shield ~30x over twelve years
  (183g to ~6,000g ask) on a book of a handful of listings; Greatsaw
  crashed when acquisition returned in 2016. Real appreciation, real
  reprint rugs.
- Rare infusions: Chak Egg Sac pinned at the 10,000g practical listing
  cap for years (above it, trade goes off-TP), 5,556g in 2026; Queen Bee
  2,792g to 580g to 9,990g to ~3,000g; Ghostly (raid-farmable) -98%.
  Lottery faucets held, farmable ones died, and every launch-week
  prestige price fell 60-80% within a year. The "infusions only go up"
  era ended around 2020.
- Permanent contracts: bank access ~300g (2013) to 3,768g (2017) to
  8,500g (2025), ~6,000g now; TP express similar shape. Roughly 30%/yr
  for a decade with 25-40% drawdowns; a visible step-change the day
  shared inventory slots launched. 2026 is an unexplained drawdown year:
  underwrite before buying, but this is the class anchor
  ([mechanism thread](https://en-forum.guildwars2.com/topic/32467-why-is-the-permanent-trading-post-express-contract-so-valuable/)).
- Precursors: Dusk 583g (2013), 1,655g peak (2014), 82g (2025). Killed
  by policy twice (crafting 2016, wizard's vault kits 2023). The
  cleanest store-of-value obituary in the game.

### Case studies that anchor the event playbook

1. Silk, Dec 2013 ascended armor: 8c to 1.87s in months (15-25x) for
   whoever front-ran the announcement; then a decade of decay. The play
   was the patch, not the hold.
2. Hardened leather, 2016-17: recipe change took it 10c to 42.7s
   (~400x); ArenaNet shipped a leather farm and it fell to 2s. Squeezes
   that get visible draw patches.
3. Mini Kasmeer, Mar 2015: datamine verified in 23 minutes, EV target
   computed with tax, ~3,000g deployed sweeping listings, exit at 2-3x
   inside 36 hours
   ([primary writeup](https://reddit.com/r/Guildwars2/comments/34mipn/market_manipulation_it_is_possible_true_story/)).
   The best surviving first-person text on GW2 corner practice.
4. Champions patch, Jan 2021: new vendor sink made Large Claws +115% in
   two hours. Sink patches move minor mats instantly; rotating
   alternatives capped it.
5. EoD, Feb 2022: mystic coin -55% in months because gen-3 legendaries
   need fewer coins. Expansions are not automatically bullish for the
   old bottleneck.
6. Mystic coin buy walls: May 2020 (nine days of bids far above the
   sell side, full reversion) and the 2021 standing 2.5g wall that
   vanished with its owner's ban (-22% overnight)
   ([thread](https://reddit.com/r/Guildwars2/comments/nh40vv/tp_baron_casiano_banned_for_dupingsuspected_rmt/)).
   Walls are not floors.
7. Research-note protection, 2022: festival bags silently lost their
   food drops. Container contents are a balance knob that turns without
   patch notes.

### Rules and boundaries

- ArenaNet's line: market plays including buyouts have never drawn a
  ban; exploits and duping/RMT have (2012 karma-weapon and snowflake
  cases; the 2021 wall owner fell on the duping side). No evidence ANet
  ever trades the TP itself; its levers are faucets, sinks, recipes,
  drop tables ([official 2012 statement](https://www.guildwars2.com/en/news/john-smith-on-the-state-of-the-guild-wars-2-economy/)).
- Structural caps: 200,000g wallet, 1,000g guild-bank tab, 500g/week
  mail transfers, ~10,000g practical listing pin. Whale wealth is forced
  into items, which is the standing bid under contracts and infusions.
- Practice folklore that survives contact with the data: accumulate via
  buy orders during lulls and gluts (prestige-item orders sit for
  months), instant-buy only on a live catalyst, sell into strength and
  events, spread targets 25-30% for holds against the 17.6% break-even.
- The serious-trader hub is the Overflow Trading Community discord;
  tooling overlaps our inventory above.
- Verify data that moves you: the community caught an apparently
  falsified 100k-bag drop dataset posted ahead of a suspected
  accumulation ([expose](https://reddit.com/r/Guildwars2/comments/dl3nq6/250_000_trick_or_treat_bags_live_value/)).

### Failure modes, each with a body attached

1. Policy risk (the killer): precursors, coins, silk, leather, dye.
2. Reprint rugs on "discontinued" cosmetics (Greatsaw, Mini Karka,
   Unidentified Dye -95%).
3. Illiquidity mistaken for price: a 6,666g ask on a book of five is not
   a mark.
4. Fake walls and disinformation campaigns (map-chat pumps date to
   2013).
5. Festival re-supply (candy corn 2014, ToT corner 2016).
6. Announcement head-fakes (the 2013 ecto livestream spike reverted in
   hours).
7. Counterparty contamination: buying from a duper's wall risks
   clawback.
