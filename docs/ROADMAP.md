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

Report (report.yml, hourly at :15): GitHub Pages, top 50 by EV/day, sortable table plus data.csv/data.json. Speculative seasonal picks arrive with M4.

### M3: depth + competition features

- `/v2/commerce/listings` order-book depth for shortlisted items.
- Undercut/outbid frequency per item; filter out penny-war items.
- Depth-aware pricing (price into book gaps, not best plus 1c).
- Realized-exit pricing by walking the book.

### M4: seasonality + event calendar

- Seasonal decomposition (STL) on multi-year price/volume series.
- Event calendar from the wiki: Wintersday, Halloween, SAB, Lunar New Year, patch/expansion dates.
- Output per candidate: buy window, sell window, historical return per past cycle.
