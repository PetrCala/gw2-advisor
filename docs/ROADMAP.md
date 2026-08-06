# Roadmap

## Locked decisions

- Zero budget. Everything runs on free tiers: GitHub Actions (cron collector), GitHub Releases (bulk data), GitHub Pages (reports).
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
- Storage: raw snapshots would grow the repo by tens of MB/day. Decide in M1 between (a) delta-encoded snapshots (store only changed prices) committed to a dedicated data branch with periodic compaction, (b) GitHub Releases as blob storage for compacted parquet/csv.gz batches, or (c) both: deltas in git short-term, compacted batches promoted to Releases.

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
