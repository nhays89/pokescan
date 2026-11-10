---
name: debug-missed-or-noisy-listings
description: Diagnose why expected listings did not show up, why there are too many, or why polling is failing or costly. Use when the user says a card was missed, notifications stopped, or the API budget runs out.
---
1. Read the tail of `data/pokescan.log`; look for "MISSED", "backfill_max_pages", "budget", "failing", and eBay errors.
2. Query `poll_runs` (errors, calls, fetched, new) and `api_usage` in `data/pokescan.db` (queries in `docs/RUNBOOK.md`).
3. Missed a specific listing: was it older than `lookback_days`? dropped by `exclude_terms`? title lacking the keyword/year? filtered by price/buying option, or dropped by a blank item specific (Language/Year)? Check with `python -m pokescan.search "<title words>"` (no DB writes) and `listings.raw_json` for what eBay actually returned.
4. Too many: raise `price_min`, narrow years, add `exclude_terms`. Too costly: lengthen intervals, lower paging, read the `check --dry-run` estimate, lower `max_analyses_per_day`.
5. Auto-fails to audit: `select item_id, defects_json from assessments where model='rule'`.
6. If the cause was a wrong assumption about eBay, update `docs/VERIFY.md`.
