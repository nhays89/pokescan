# Architecture

## Flow
```
searches.yaml --(ConfigLoader, hot reload)--> Scheduler tick (5 s)
  for each enabled search due by priority interval  [year/set searches + watchlist cards as "watch:<name>"]
    SearchPoller.poll -> EbayBrowseClient.search (page 1..N, newest first) -> RateBudget.consume per call
      per item: drop if older than lookback cutoff / matches exclude_terms
      ListingRepository.upsert  (new = item_id not in DB)         -> listings, listing_matches, listing_images
      stop paging at: already-seen item | too-old item | short page | max_pages
    new listings -> handlers:
      ConsoleNotifier        prints NEW lines
      WatchNotifier          pushes "WATCH: <name>" for watch hits (unless the card analyzes and analysis is on)
      AnalysisHandler.handle enqueue (watch hits only if the card has analyze: true)
AnalysisHandler.run (queue worker, separate task so polling never waits)
  process: auto-fail by title (japanese, jp, ...) -> skip terms (not for watch) -> daily cap
         -> getItem (all photo URLs + item specifics; Language aspect set and not allowed => auto-fail, blank passes)
         -> card_details upsert (aspects first, title-parse fallback)
         -> ImageStore.download (1600px variant, falls back to original)
         -> analyzer.analyze -> QualityPolicy.evaluate -> assessments row
         -> notify (PASS / BACK OK / REVIEW; watch hits notify on any verdict)
Scheduler alerts: 3 consecutive failures -> one push, recovery -> one push, budget exhausted -> one push/day
```

## Modules
| Module | Responsibility |
|---|---|
| `pokescan/ebay/auth.py` | OAuth client-credentials token, cached, refreshed ~5 min before expiry |
| `pokescan/ebay/client.py` | `search()`, `get_item()`; retries 5xx/network, one re-auth on 401, raises RateLimited on 429 |
| `pokescan/ebay/query.py` | `expand_queries` (set batches, year-in-title mode), `build_params` (filter, aspect_filter, category), `full_res` |
| `pokescan/pipeline/poller.py` | paging, dedup, lookback cutoff, seed mode (lookback 0), warns when page cap is hit without overlap |
| `pokescan/pipeline/scheduler.py` | interval loop, `run_once`, failure/budget alerts |
| `pokescan/analysis/classical.py` | OpenCV: find card, warp to 630x880, detect BACK (blue ring), 8 edge/corner zones, glare mask, whitening score |
| `pokescan/analysis/policy.py` | `ConditionReport` -> PASS / FAIL / REVIEW using `PolicyConfig` |
| `pokescan/analysis/claude_vision.py` | optional vision-model analyzer returning the same `ConditionReport` |
| `pokescan/domain/card_meta.py` | `extract_card_details(title, aspects)` |

## Data model (SQLite, WAL)
`listings` (PK item_id, raw_json kept), `listing_images`, `listing_matches` (search_name per item), `poll_runs`, `api_usage` (per UTC day),
`assessments` (verdict/grade/defects_json, `model='rule'` for auto-fails), `card_details`, `notifications` (dedupe),
view `matches` = passed/review assessments joined with card info.

## Classical analyzer essentials
- Judges the BACK only (`scope='back_only'`); PASS means "back clear, now look at the front yourself".
- Card outline: Canny with glare-blob edges removed -> contour -> per-side line fit with tightening tolerances, biased to the
  outermost contour (whitening only shrinks the visible card). Untrusted outline => not found => REVIEW.
- Whitish = low saturation and bright, adaptive to the card's own blue. Glare core = blown-out, thick, reaches into the card,
  grown by a halo; zone mostly under glare => unassessed. Results merge across back photos (worst usable observation).
- Severity thresholds on the whitish fraction: minor/moderate/major = 0.03 / 0.12 / 0.30 (uncalibrated).

## Config (config/searches.yaml)
`global` (budget, category, languages, aspects, year_match, paging, lookback, intervals) - `searches` (years and/or sets, price/buying filters,
exclude_terms, priority) - `watchlist` (defaults + cards) - `analysis` (enabled, analyzer, caps, skip/auto-fail terms, allowed_languages,
classical tunables, policy). Reference with comments is the sample file itself.
