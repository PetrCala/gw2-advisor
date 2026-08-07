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

Follow-ups (post-M4, issue-tracked): timing verdicts, pay-up-to ceiling, suggested quantity, action queue and festival countdown on the report, published as `actions.json` (#16); notifications on queue transitions (#17); holdings-aware personal queue as M5 (#18). Verdicts recompute at every report build against the fresh snapshot; the weekly artifact only carries the pattern evidence.
