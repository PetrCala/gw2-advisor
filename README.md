# gw2-advisor

Trade advisor for the Guild Wars 2 trading post. Collects price data, computes velocity and fill-time features, and produces a daily list of flip and speculation candidates for manual in-game trading.

Zero-budget design: data collection runs on GitHub Actions cron, storage lives in git and GitHub Releases, reports publish via GitHub Pages.

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
