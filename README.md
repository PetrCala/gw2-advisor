# gw2-advisor

[![collect](https://github.com/PetrCala/gw2-advisor/actions/workflows/collect.yml/badge.svg)](https://github.com/PetrCala/gw2-advisor/actions/workflows/collect.yml)

Trade advisor for the Guild Wars 2 trading post. Collects price data, computes velocity and fill-time features, and produces a daily list of flip and speculation candidates for manual in-game trading.

Near-zero-budget design: data collection runs on GitHub Actions cron, price history lives in a size-capped S3 bucket (expected cost well under $1/month), reports publish via GitHub Pages.

## Layout

- `collector/` polls the GW2 commerce API on a schedule
- `db/` snapshot storage, schema, compaction
- `features/` velocity, fill time, undercut rate, book depth
- `scorers/` flip EV ranking and seasonal signals
- `report/` daily HTML/CLI report
- `archive/` preserved third-party data, see its README for provenance

## Status

M0 (bootstrap). See [docs/ROADMAP.md](docs/ROADMAP.md) and the issue tracker.

## Compliance

The GW2 commerce API is read-only; all trades are placed manually in game. Advisory tooling of this kind is permitted under ArenaNet's terms; automating in-game actions is not, and this project doesn't.

## License

MIT. The `archive/` directory contains preserved data credited to its original authors.
