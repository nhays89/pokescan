---
name: add-search-or-watch-card
description: Add or change a year/set search or a watchlist card in config/searches.yaml and sanity-check cost and requests before running. Use when the user wants to track new cards, years, or sets.
---
1. Decide the type: broad discovery (year/set search under `searches:`) vs a specific card the user always wants to hear about (`watchlist.cards`, notifies on every new listing).
2. Edit `config/searches.yaml`. Searches: `years`/`sets`, `filters.price_min` (cheap commons dominate volume), `exclude_terms`, `priority`. Watch cards: `name`, `query` (quote phrases), `max_price`, optional `analyze`.
3. Run `python -m pokescan.check --dry-run`: confirm the request params and the "Estimated steady-state polling cost" against `daily_call_budget` (each query costs about 1440 / interval-minutes calls per day).
4. Run `python -m pokescan.main --once --search "<name or watch:name>"` and read the "first run: N new listings" log line. Too many => raise the price floor, narrow years, add exclude terms.
5. Do not widen `lookback_days` casually; the window also applies after downtime.
