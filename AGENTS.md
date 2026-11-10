# pokescan

Long-running Python worker that polls the eBay Browse API for newly listed vintage Pokemon cards, stores them in
SQLite, grades condition from the listing photos, and pushes a phone notification (ntfy) for cards worth a look.
Personal tool, single user, runs on a Windows machine. Not a web server.

## Commands (run from the project root)
- Setup: `py -3.12 -m venv .venv` -> `.venv\Scripts\activate` -> `pip install -r requirements.txt` -> copy `.env.example` to `.env`
- Tests: `python -m pytest -q tests` (all offline; no eBay/Anthropic calls). Must stay green.
- Show the exact eBay requests + estimated API cost, no keys needed: `python -m pokescan.check --dry-run`
- Live smoke test (keys needed, a few API calls): `python -m pokescan.check`
- Poll once and exit: `python -m pokescan.main --once [--search NAME]` (watchlist cards are named `watch:<name>`)
- Run the polling loop: `python -m pokescan.main`
- One-off lookup, no DB writes: `python -m pokescan.search "dark golduck" --max-price 300`

## Layout
`config/searches.yaml` is the file the user edits (hot-reloaded). `pokescan/config/` is the code that validates it (don't confuse them).
- `pokescan/ebay/` auth (client-credentials token), `client.py` (the ONLY place that calls eBay), `query.py` (config -> API params), `models.py`
- `pokescan/pipeline/` `poller.py` (paging/dedup/lookback), `scheduler.py` (interval loop, alerts), `budget.py` (daily call cap), `filters.py`
- `pokescan/analysis/` `handler.py` (queue worker), `classical.py` (OpenCV back-whitening), `claude_vision.py`, `policy.py` (verdict rules), `base.py` (ConditionReport)
- `pokescan/storage/` `db.py` (schema), `repositories.py`, `image_store.py`; `pokescan/notify/handlers.py`; `pokescan/domain/` (Listing, card metadata)
- Entry points: `main.py`, `check.py`, `search.py`. Tests in `tests/`. Details: `docs/ARCHITECTURE.md`.

## Rules that must not be broken
1. Every eBay call goes through `EbayBrowseClient._get`, which consumes the persisted daily budget. Never call eBay or hardcode a limit anywhere else.
2. Glare must never cause a FAIL. It is masked, zones it hides are "not checked", and ambiguity resolves to REVIEW. A missed good card is worse than an extra manual look.
3. A card the classifier cannot localize or judge must never PASS. Errors fall toward REVIEW.
4. Config errors never crash the daemon: the loader keeps the last good config. New config fields need defaults and a test.
5. Watchlist hits (`search_name` starting `watch:`) are never silent and bypass `skip_title_terms`.
6. Secrets live only in `.env`. Never log keys; never put them in YAML.
7. Keep the two model layers separate: eBay response shapes in `pokescan/ebay/models.py`, internal `Listing` in `pokescan/domain/models.py`.

## Gotchas
- Many eBay field names/filter formats are from memory and NOT yet verified against the live API. See `docs/VERIFY.md` before relying on them, and update it when you confirm or correct one.
- The classical analyzer's thresholds were only tested on synthetic images. Calibrate on real photos (`analysis.classical.debug_dir`).
- Windows: no `loop.add_signal_handler`; console output is forced to UTF-8 (titles contain Japanese/emoji). Keep both.
- Use `python -m pytest` from the root; some tests import from each other (`tests/` is on the path).
- Budget math: each query costs 1 call per page per poll; each analyzed listing costs 1 extra `getItem` call.

## Docs
`docs/ARCHITECTURE.md` (how it fits together) - `docs/DECISIONS.md` (why) - `docs/VERIFY.md` (unverified assumptions, living checklist)
- `docs/RUNBOOK.md` (operate/troubleshoot) - `docs/BACKLOG.md` (not built yet). Task recipes: `.claude/skills/`.
