# Roadmap

## Locked decisions

- Near-zero budget. Compute runs free on GitHub Actions (cron collector) and GitHub Pages (reports). Storage is S3 with an accepted budget of $1-5/month and a design that keeps actual cost around $0.15-0.30/month; growth is capped by construction (see M1).
- Collector runs on GitHub Actions cron. Scheduling jitter (runs delayed by minutes, occasionally skipped) is accepted; features must tolerate irregular snapshot spacing.
- Trades are executed manually in game. The tool only advises. The commerce API is read-only, so this is also the only option.
- Python for collector, features, and report. R is an option later for seasonal modeling (M4).

## Milestones

### M0: bootstrap (done)

Repo, scaffold, roadmap, issues, and the gw2profits.com data archive (site shut down 2026-08-15; recipe/salvage dump preserved under `archive/gw2profits/`).

### M1: collector + storage (done)

Poll `/v2/commerce/prices` for all tradable items (~27k, 135 paginated requests at 200/page, well under the 300 req/min limit) on a cron workflow every ~10 min.

GitHub Actions specifics to handle:

- Cron jitter: stamp every snapshot with the actual fetch time; never assume fixed intervals.
- Scheduled workflows are disabled after 60 days without repo activity. The collector's own commits count as activity, so this self-solves while it runs; add a health check anyway.
Storage (decided): S3, private bucket `gw2-advisor-data-petrcala` in us-east-1.

- `raw/YYYY/MM/DD/HHMMSS.json.gz`: delta snapshots (changed prices only), lifecycle-expired after 30 days, bounding the prefix at roughly 1GB
- `state/latest.json.gz`: full price table, overwritten each run, used for diffing
- `compact/YYYY-MM-DD.parquet`: daily compaction of raw deltas, the durable store, ~6GB/year
- S3 has no native byte quota, so the cap is by construction: the workflow role is the only writer, and the collector refuses any upload that would push the bucket past 25GB (~$0.60/month) or any single object past 64MB
- Public access fully blocked, so there is no public-egress cost vector; our own Actions reads sit inside the 100GB/month free egress tier
- Auth via GitHub OIDC (no long-lived keys); the role can touch only this bucket, trust is limited to this repo's main branch. Setup in `infra/setup_aws.py`

### M2: backfill + flip scorer v1 + report (done)

Data sources (decided):

- datawars2.ie mirrors TP data back to 2012-10: daily price/quantity extremes from the start, fill and churn counts (`buy_sold`, `sell_sold`, listed/delisted) from ~2019. Its `buy_sold` counts units filled against buy orders (our buy side), `sell_sold` units bought off sell listings (our sell side).
- One-time backfill (`collector/backfill.py`, backfill.yml): full daily history for every tradable id into `history/dw2/chunk-NNNN.parquet`, 100 ids per chunk, 10 ids per request, throttled, resumable by skipping existing keys. Roughly 280 objects, one to two GB. This feeds M4 seasonality; the daily scorer does not read it.
- The daily scorer reads the datawars2 item snapshot (names, vendor floors, 1d/7d/1m rolling fill counts; one request) plus our own `state/latest.json.gz` for fresher prices and queue sizes.

Flip scorer v1 (assumptions are constants in `scorers/flip.py`):

- queue-front placement (buy +1c, sell -1c), 15% TP cut
- we capture 25% of a side's daily filled flow while at the front
- EV/day = after-fee margin / per-unit cycle time (independent of lot size; capital recycles); suggested qty capped by 100g capital per item and a 2-day round trip
- filters: both sides fill at least 24 units/day (7d average), margin at least 5% of cost, sell above vendor floor
- confidence from three checks: spread survives on 7d average prices, 7d sell band under 15%, yesterday's flow within 2x of the weekly average on both sides

Report (report.yml, daily 04:10 UTC after compaction, dispatchable on demand): GitHub Pages, top 50 by EV/day, sortable table plus data.csv/data.json. Speculative seasonal picks arrive with M4. (Ran hourly briefly on 2026-08-06, reverted to daily.)

### M3: depth + competition features (done)

- `gw2api.fetch_listings` pulls order books for the ~200-item v1 shortlist (one request per 200 ids).
- Depth-aware pricing (`features/book.py`): instead of best +-1c, prices walk into book gaps, accepting at most 0.25 days of queue ahead of us at the item's flow; the queue wait counts against the round-trip budget.
- Exit floor: worst-case loss per unit from dumping the whole lot into the current buy book, shown as Exit % in the report.
- Undercut/outbid rates (`features/reprice.py`): per-item 1-2c best-price moves per day, counted from our own delta snapshots (compact/ parquet plus today's raw). Items above 150 combined reprices/day are dropped as penny wars. Needs delta history in S3, so the columns stay empty in `--local` builds.
- Deferred: feeding per-item reprice rates back into the capture assumption (waits for a few weeks of delta history).

### M4: seasonality + event calendar (done)

Decisions:

- Python with statsmodels STL, not R. The R option stays closed unless the modeling outgrows STL.
- Static event calendar checked into the repo (`season/events.py`): per-year run dates for Wintersday, Halloween, Super Adventure Festival, Lunar New Year, Dragon Bash, and Festival of the Four Winds, hand-copied from the wiki, plus expansion launch dates. Festivals shift dates yearly, so event-linked candidates anchor their windows to each year's actual dates.
- Heavy computation runs in its own weekly workflow (season.yml, Mondays 03:20 UTC, dispatchable): streams all `history/dw2/` chunks, filters rows against a snapshot-derived candidate set (liquidity and price floors, capped at 4000 items so runtime stays well inside the 1-hour OIDC session), and writes a ~10 KB `season/latest.json.gz`. The daily report reads only that artifact, non-fatally, so the report never blocks on seasonality.
- Pipeline per item: daily sell prices (pre-2019 rows carry only min/max extremes, so their midpoint fills in), weekly log-price resample, STL (period 52, robust) over the last 8 years, buy window at the seasonal trough and sell window at the peak, then the realized return of every completed past cycle: buy at the median observed price in the buy window, sell at the median in the sell window, net of the 15% cut. Cycles missing observed prices in either window are dropped, not guessed, which handles the sparse 2014-15 mirror era and young items honestly.
- Evidence-first display: the report's second table shows cycle count, per-year returns (hover), hit rate, worst and latest cycle, and expansion launches that overlapped a cycle, so a 3-cycle pattern is visibly weaker than a 12-cycle festival cycle. Score = median return times hit rate, damped below 6 cycles.
- gw2profits forge/salvage conversion paths stay out of scope; the archived recipes remain available for a later milestone.

Paper trading (#30, `report/track.py`, a non-gating report.yml step): each build's flip picks and buy-now seasonal rows are opened as paper positions in `track/` state, replayed daily against the datawars2 snapshot, and booked to `site/track.html`. A flip leg fills only on days the market's touch reached our price, taking the assumed capture share of that day's flow; unsold units are marked at the last bid net of fees. Fills are simulated, so the measurable claims are placement reachability (an order that never reaches the front is the unbounded-gap-walk failure of #29, caught automatically) and capture needed, the share of the flow past our order that the lot would have demanded inside the predicted round trip.

Follow-ups (post-M4, issue-tracked): timing verdicts, pay-up-to ceiling, suggested quantity, action queue and festival countdown on the report, published as `actions.json` (#16); notifications on queue transitions (#17); holdings-aware personal queue as M5 (#18). Verdicts recompute at every report build against the fresh snapshot; the weekly artifact only carries the pattern evidence.

### M6: long-horizon investment book

Capital-scaling layer: strategies holding weeks to years (release basket, patch-event theses, festival carry at size, luxury market making, vault holds, corners, structural-bound arbitrage, time-gated carry, guarded reversal, release-lifecycle cohorts), a bankroll/risk framework, and a turnover-weighted market index to benchmark against. Full design and sub-milestones M6.a-M6.e in [INVESTING.md](INVESTING.md); supporting research inventory in [RESEARCH.md](RESEARCH.md).

M6.a foundations (built). Decisions:

- Market index (`invest/index.py`, weekly invest.yml, Mondays 01:40 UTC): turnover-weighted daily index over the trailing top 500 items, constituents and weights rebalanced monthly from prior-month sell-side turnover, single item capped at 5% (ecto alone is ~15% of turnover), relatives clipped 0.1-10x against data glitches, levels renormalized over whatever traded each day. Artifact `invest/bench.json.gz` also carries the full ecto series (to 2012) and the gem rate (gw2tp history to 2013 plus the official spot).
- The index starts mid-2019, not 2013 as designed: datawars2 rows before then carry only price extremes, no fills and no quantities (verified empirically), so turnover weighting cannot reach further back and no other weighting is defensible either. The ecto and gem lines carry the older context.
- NAV series (`invest/nav.py`, daily in report.yml): one point per UTC day, the whole account (via `account.snapshot.gather`) valued at dump-into-bid net of fees, stock broken down by material class (`invest/tags.py`: T6, festival, staples, luxury by 500g+ price rule). Needs a GW2_API_KEY repository secret; skips cleanly without one. Absolute gold lives only in the private bucket; the public page (`report/invest.py`, site/invest.html) rebases NAV to 100 and shows shares, never gold.
- Bankroll is a repository Actions variable (BANKROLL_G), not a file in the public repo; `invest/bankroll.py` holds the per-strategy allocation caps (release 30%, festival 25%, luxury 25%, vault 15%, corners 10%), which start binding when strategies emit positions (M6.b+).

M6.b festival at size + craft carry (built). Decisions:

- Festival sizing (S3): `season/actions.enrich_all` replaces the flat 50g-per-pick `SEASON_CAPITAL` with the festival bankroll cap (25% of BANKROLL_G) split evenly across whatever picks are currently buying or opening soon, still bounded by the existing capture and exit-book caps; unset BANKROLL_G degrades to the flat constant unchanged.
- Craft carry (S8, `invest/craft.py`, daily in report.yml): four of the five daily-cooldown Mystic Forge materials (Lump of Mithrillium, Glob of Elder Spirit Residue, Spool of Thick Elonian Cord, Spool of Silk Weaving Thread) are account-bound themselves but refine, uncapped, straight into one tradable tier-7 material each (Deldrimor Steel Ingot, Spiritwood Plank, Elonian Leather Square, Bolt of Damask); the fifth, Charged Quartz Crystal, has no single tradable next step (21 recipes consume it) and is tracked for input cost only. Pricing is queue-front like `scorers.flip` (buy 1 above the best bid, sell 1 below the best ask); a refinement is marked dead once its spread drops under the 15% tax itself. Daily points land in `invest/craft.json.gz`; `report/invest.py` shows the latest reading plus a trailing 30-day live/dead tally.
- Paper tracking: every opened position (`report/track.py`) now carries a `strategy` tag (flip, festival carry, time-gated crafting) so a future scoreboard can group by strategy, not just by kind; a live craft-carry reading opens a weekly paper position exactly like a flip or seasonal pick, resolved after 7 days against that week's own cost and revenue.

M6.c luxury desk (built). Decisions:

- Universe (`invest/luxury.py`): items priced 500g+ with at least one sale in the trailing week from the dw2 snapshot, minus a curated gen-1-precursor exclusion list (ids 29166-29185; wizard's vault free kits and a policy-broken price series since 2023), ranked by liquidity and capped at 160; live count sits around 120-170 depending on which side's fill count is counted.
- Daily book collection: `invest.luxury.main()` runs in report.yml's build job, no GW2_API_KEY needed, fetches order books for the universe in one `gw2api.fetch_listings` page, and stores them dated under `invest/luxury/books/YYYY-MM-DD.json.gz` (kilobytes/year, never pruned).
- Velocity blends the dw2 7-day and 1-month rolling fill counts per side so one heavy or dead day cannot swing a 1-5-a-week estimate; `book_diff_velocity` cross-checks it against day-over-day book-depth drops once a few days of our own collection accumulate (informational, an upper bound: depth also falls on a cancellation, not only a fill).
- Placement reuses `features/book.py`'s gap walk for both legs (patient bid, relist ask), sized by risk rule 2 (capture x exit flow x 30 days) and a hard 1-3-unit-per-item cap; margin outside 10-75% is dropped, below because it is not this strategy, above because a live scan found real spreads topping out near 65% before a break into two- and three-listing books - illiquidity mistaken for price, not an edge.
- A resting order unfilled past 21 days (`invest.luxury.PATIENCE_DAYS`) is a reprice-or-withdraw candidate by rule, not mood; `report/track.py`'s own paper positions flag this as `went_stale` rather than actually repricing mid-simulation, since the daily snapshot it ticks against carries no order book to reprice from.
- Renders as a table on the benchmarks page (`report/invest.py`, site/invest.html) and opens paper positions per pick in `report/track.py` under the `"luxury desk"` strategy tag (`STRATEGY["luxury"]`), alongside the flip, festival carry and time-gated crafting simulations.

M6.d event layer (built). Decisions:

- Thesis registry (S2, `invest/theses.yaml` + `invest/thesis.py`): a git-tracked, hand-edited YAML list (ships empty), one entry per item leg, enriched fresh at report-build time against the live snapshot and a small targeted book fetch; opens/ticks/closes as a fifth `report/track.py` paper-position kind (`STRATEGY["thesis"]`) once a leg's price is at or under its `entry_ceiling`; a catalyst-passed or invalidation-date-passed transition nags once via the existing trade-actions issue and RSS feed (`report/notify.py`), and every entry always renders on `site/invest.html` regardless of bucket so a missed nag is never the only way to see current state; `avoid`-direction entries are notify-only, never sized or tracked. New bankroll cap: thesis registry 20% (`invest/bankroll.py`), provisional pending sign-off.
- Market radar (S2, `invest/radar.py`, daily in report.yml, no API key needed): three cheap screens off the dw2 snapshot mirror - an abnormal-activity z-score radar (price on daily log-return, sold/listed/delisted on raw daily counts, both book sides summed, flagged at |z| >= 4 against a self-collected rolling ~90-day baseline with a minimum-observation floor of 5), a pump filter (4-of-5-days up >= 4% on low resting depth), and a sink-anticipation watchlist (vendor-floor-pinned items clearing a 100-units/day 7-day sell-flow floor, needs no history at all). The radar's own history is self-collected going forward starting from this milestone; there is a real burn-in period (the z-score screen has no output for its first ~90 days) rather than a backfilled bootstrap, by design.
- Release event study (S1, `invest/release_study.py`, weekly in invest.yml as a second step alongside `invest.index`): per-launch tax-adjusted returns for the two material classes with a curated id set matching S1's thesis text (T6, STAPLES); FESTIVAL/LUXURY/other are intentionally excluded (already covered by S3, no historically-valid retroactive rule, and no coherent thesis, respectively). Six launches is the hard sample ceiling; every stat always shows raw n ("4 of 6 launches priced") and per-launch id coverage, never a bare percentage, and both class rows always render regardless of n. Covers only the six full-expansion dates, not the quarterly drops S1's text also names - a named follow-up, not yet built.
- Release calendar risk flag (S1/S2 interaction, `invest/risk.py`, no artifact, no workflow step): a farmable-material open position (t6/festival/staples) gets a "Launch risk" column on its `report/track.py` open-position table when a RELEASES launch falls inside a 30-day forward window (`season.actions.OPENS_SOON_DAYS`, imported not duplicated). Paper positions only in this slice; real account holdings are not yet flagged. The black-lion/vault-rotation half of this risk flag is deferred: S5 (vault holds) has no shipped position type to attach it to yet.
