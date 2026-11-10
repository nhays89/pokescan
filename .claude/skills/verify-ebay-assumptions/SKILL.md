---
name: verify-ebay-assumptions
description: Confirm or correct the unverified eBay API assumptions (category id, item-specific names, filter formats, response fields) against the live API and record the result. Use when setting up with real keys, when eBay returns a 400/zero results, or when docs/VERIFY.md has UNVERIFIED rows.
---
1. Read `docs/VERIFY.md`; list rows still UNVERIFIED.
2. Run `python -m pokescan.check --dry-run`, then `python -m pokescan.check` (needs `.env` keys). Capture the output.
3. From the output decide each row: categories seen vs `183454`; item-specific names/values (Year, Language, Game); dates present on results; any 400 body.
4. If a name or format is wrong, fix it in `config/searches.yaml` (e.g. `year_aspect_name`, `aspects`) or `pokescan/ebay/query.py`, add/adjust a test in `tests/test_offline.py`, run `python -m pytest -q tests`.
5. For `getItem`-dependent rows, fetch one listing's JSON (`EbayBrowseClient.get_item`) and compare with the `localizedAspects` / `additionalImages` handling in `pokescan/analysis/handler.py`.
6. Update the Status column in `docs/VERIFY.md` with CONFIRMED/WRONG and the date. Never mark CONFIRMED from memory.
