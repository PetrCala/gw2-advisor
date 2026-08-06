# Roadmap

## Locked decisions

- Near-zero budget. Compute runs free on GitHub Actions (cron collector) and GitHub Pages (reports). Storage is S3 with an accepted budget of $1-5/month and a design that keeps actual cost around $0.15-0.30/month; growth is capped by construction (see M1).
- Collector runs on GitHub Actions cron. Scheduling jitter (runs delayed by minutes, occasionally skipped) is accepted; features must tolerate irregular snapshot spacing.
- Trades are executed manually in game. The tool only advises. The commerce API is read-only, so this is also the only option.
- Python for collector, features, and report. R is an option later for seasonal modeling (M4).

## Milestones

### M0: bootstrap (done)

Repo, scaffold, roadmap, issues, and the gw2profits.com data archive (site shut down 2026-08-15; recipe/salvage dump preserved under `archive/gw2profits/`).

### M1: collector + storage

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

### M2: backfill + flip scorer v1 + report

- Backfill multi-year history from the datawars2.ie public API.
- Flip scorer: margin after 15% tax, velocity estimate from listing deltas, fill-time estimate (queue ahead / velocity), EV/day ranking.
- Daily report published via GitHub Pages: item, buy at, sell at, qty, expected round-trip time, EV/day.

### M3: depth + competition features

- `/v2/commerce/listings` order-book depth for shortlisted items.
- Undercut/outbid frequency per item; filter out penny-war items.
- Depth-aware pricing (price into book gaps, not best plus 1c).
- Realized-exit pricing by walking the book.

### M4: seasonality + event calendar

- Seasonal decomposition (STL) on multi-year price/volume series.
- Event calendar from the wiki: Wintersday, Halloween, SAB, Lunar New Year, patch/expansion dates.
- Output per candidate: buy window, sell window, historical return per past cycle.
