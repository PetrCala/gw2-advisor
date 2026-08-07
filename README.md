# gw2-advisor

[![collect](https://github.com/PetrCala/gw2-advisor/actions/workflows/collect.yml/badge.svg)](https://github.com/PetrCala/gw2-advisor/actions/workflows/collect.yml)

Trade advisor for the Guild Wars 2 trading post. Collects price data, computes velocity and fill-time features, and produces a daily list of flip and speculation candidates for manual in-game trading.

Near-zero-budget design: data collection runs on GitHub Actions cron, price history lives in a size-capped S3 bucket (expected cost well under $1/month), reports publish via GitHub Pages.

## Layout

- `collector/` polls the GW2 commerce API on a schedule
- `db/` snapshot storage, schema, compaction
- `features/` velocity, fill time, undercut rate, book depth
- `scorers/` flip EV ranking
- `season/` seasonal decomposition, event calendar, cycle returns
- `report/` daily HTML/CLI report
- `account/` your own trading results, needs an API key
- `archive/` preserved third-party data, see its README for provenance

## Checking your own trading

Copy `.env.example` to `.env` and put a GW2 API key in it, then:

```
python -m account.window --hours 5     # what your trading did in that window
python -m account.snapshot             # value the account, diff against the last one
```

`account.window` reads completed trading post transactions and marks the stock
they moved against the live order book, so it separates cash that moved from
value that moved. Buying 100g of stock drops cash by 100g while account value
barely moves, and the window report says so instead of reporting a 100g loss.

The window covers at most the last 90 days, the limit of what the transaction
history endpoint keeps.

`account.snapshot` is the baseline half. No public API serves your account
value as it stood in the past, not the official one and not gw2efficiency, so
account value deltas are only available between snapshots you have taken.
Snapshots are local and git-ignored.

## Daily report

The flip shortlist publishes to [petrcala.github.io/gw2-advisor](https://petrcala.github.io/gw2-advisor/) daily (04:10 UTC, plus GitHub cron jitter). A second table lists speculative seasonal picks (buy window, sell window, and the return of every past cycle), recomputed weekly by season.yml from the multi-year history. Trigger an off-schedule rebuild anytime with `gh workflow run report.yml`. Build it locally without AWS access:

```
pip install -r collector/requirements.txt
python -m report.build --local
```

## Status

M4 (seasonality + event calendar). See [docs/ROADMAP.md](docs/ROADMAP.md) and the issue tracker.

## Compliance

The GW2 commerce API is read-only; all trades are placed manually in game. Advisory tooling of this kind is permitted under ArenaNet's terms; automating in-game actions is not, and this project doesn't.

## License

MIT. The `archive/` directory contains preserved data credited to its original authors.
