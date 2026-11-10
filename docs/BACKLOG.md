# Backlog (designed or discussed, not built)

- Retry listings the daily analysis cap cut off (they are stored as seen and never retried). Needs attempt tracking so a listing with no usable images does not burn `getItem` calls forever.
- Reserve part of the API budget for polling so analysis `getItem` calls cannot starve it.
- Batch several watchlist cards into one OR-query and attribute hits by title (cuts calls; per-card price caps become client-side).
- Replay script: re-score stored images against new thresholds without new listings or API calls.
- Second stage: send classical PASS cards to the vision model to assess the front.
- Detect non-blue (Japanese/other) backs by color and auto-fail.
- Ignore relisted duplicates (new item id, same card/seller/price).
- Optional server-side `itemStartDate` filter once verified.
- Dashboard (FastAPI) over `matches`; move to an always-on host (the PC sleeping stops polling and the failure alerts).
