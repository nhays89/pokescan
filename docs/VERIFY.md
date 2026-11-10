# Unverified assumptions (living checklist)

Most of this project was built and tested offline. These items come from memory of eBay's docs or from synthetic tests and
need confirming against the live API / real photos. When you confirm or correct one, edit the status and date here.
Status: UNVERIFIED | CONFIRMED | WRONG (with the fix).

| # | Assumption | Where it matters | How to verify | Status |
|---|---|---|---|---|
| 1 | Category `183454` is "CCG Individual Cards" (all TCGs, not just Pokemon) | `global.category_ids` | `python -m pokescan.check` lists categories seen | UNVERIFIED |
| 2 | Item specific names: `Year Manufactured`, `Language`, `Game` (value for Pokemon unknown) | `year_aspect_name`, `languages`, `aspects` | check prints "Item specifics eBay offers" (one probe call) | UNVERIFIED |
| 3 | `aspect_filter=categoryId:X,Name:{A|B}` format; requires exactly one category | `pokescan/ebay/query.py` | live search; eBay's 400 body names the bad part | UNVERIFIED |
| 4 | `filter=price:[a..b],priceCurrency:USD,buyingOptions:{A|B}`; open-ended `price:[..b]` and `price:[a]` | `pokescan/ebay/query.py` | live search | UNVERIFIED |
| 5 | Search results include `itemCreationDate` per item | lookback window | check prints dates ("no date" if absent) | UNVERIFIED |
| 6 | `getItem` returns `image`, `additionalImages`, `localizedAspects[{name,value}]` | analysis handler, card_details, Language check | inspect one `getItem` JSON | UNVERIFIED |
| 7 | Search results are capped at ~10,000 per query | broad searches, backfill | read the Browse API docs | UNVERIFIED |
| 8 | Default Browse limit ~5,000 calls/day; Analytics `getRateLimits` URL | `daily_call_budget`, check | check prints the real limit if the endpoint works | UNVERIFIED |
| 9 | Swapping `s-l225` -> `s-l1600` in image URLs yields full-res | `full_res()` in `pokescan/ebay/query.py`, ImageStore | download one image, check size | UNVERIFIED |
| 10 | `itemStartDate` server-side filter exists (not used) | possible optimization | live search with the filter | UNVERIFIED |
| 11 | Production keysets must handle marketplace account deletion notifications or get the exemption | running with prod keys | eBay developer portal | UNVERIFIED |
| 12 | Classical thresholds (0.03/0.12/0.30), blue-ring back detection (>=0.55), blur_min (15) work on real photos | `analysis.classical` | run with `debug_dir`, inspect `_zones.jpg`, compare to eyeballed condition | UNVERIFIED (synthetic only) |
| 13 | Claude analyzer prompt/model default `claude-sonnet-5-5` and JSON parsing | `pokescan/analysis/claude_vision.py` | one live run with `analyzer: claude` | UNVERIFIED |
| 14 | Non-blue (e.g. Japanese) backs are sent to REVIEW | classical back detection | feed a real Japanese back photo | UNVERIFIED |
