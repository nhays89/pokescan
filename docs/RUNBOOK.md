# Runbook

## First-time bring-up (in this order)
1. Install, create `.env` (App ID -> `EBAY_CLIENT_ID`, Cert ID -> `EBAY_CLIENT_SECRET`; PRODUCTION keys). `NTFY_TOPIC` optional, `ANTHROPIC_API_KEY` only for `analyzer: claude`.
2. `python -m pokescan.check --dry-run` - read the requests and the estimated calls/day.
3. `python -m pokescan.check` - confirms token, runs one live search per entry, lists real item-specific names. Update `docs/VERIFY.md`.
4. `python -m pokescan.main --once` - watch the "first run: N new listings" lines; tune price floors/years.
5. Run the loop with `analysis.enabled: false` for a day. Read `data/pokescan.log` and `poll_runs`.
6. Phone push: install ntfy, subscribe to an unguessable topic, put it in `NTFY_TOPIC`.
7. Enable analysis: `analyzer: classical`, `max_analyses_per_day: 20`, `classical.debug_dir: data/debug`. Calibrate (see skill `tune-classical-analyzer`), then raise the cap.
8. Make it unattended: Task Scheduler "At log on", action `<project>\.venv\Scripts\python.exe -m pokescan.main`, Start in = project folder, restart on failure; disable sleep while plugged in.

## Daily operation
- Logs: `data/pokescan.log` (rotating). Alerts arrive via ntfy: search failing x3, recovered, daily budget used up.
- Useful queries (`sqlite3 data/pokescan.db`):
  - `select * from matches order by assessed_at desc limit 20;`
  - `select search_name, started_at, calls, fetched, new_listings, error from poll_runs order by id desc limit 20;`
  - `select day, calls from api_usage order by day desc limit 7;`
  - `select verdict, count(*) from assessments group by verdict;`
- Edit `config/searches.yaml` live; changes apply within ~5 s. `analysis.model` and the analyzer choice need a restart.

## Troubleshooting
| Symptom | Likely cause / action |
|---|---|
| `check` says token rejected | Sandbox keys, or App ID / Cert ID swapped |
| HTTP 400 from search | A filter format or aspect name is wrong; the error shows the params sent. See `docs/VERIFY.md` #2-4 |
| Zero results | Over-narrow filters, or `year_aspect_name` wrong; try `year_match: title`, or loosen price |
| Log: "hit max_pages ... MISSED" | More new listings than `max_pages x page_size` between polls. Narrow the query or raise paging |
| Log: "first run stopped at backfill_max_pages" | Window holds more listings than one first-run walk; narrow query or accept |
| Everything is REVIEW "no usable back photo" | Sellers' photos lack a clear back, or back detection fails on real photos; inspect debug images |
| Gaps after the PC was off | Lookback window shorter than the downtime; set `lookback_days` larger before restarting |
| No notifications | `NTFY_TOPIC` unset (console only), or app not subscribed to that topic |
| Budget exhausted early | Too many searches/pages, or analysis cap too high; see estimate in `check --dry-run` |
