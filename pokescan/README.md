# pokescan
Phase 1: polls eBay Browse API for newly listed cards, dedupes into SQLite, notifies you.
Phase 2: downloads photos, has Claude grade condition, notifies only on PASS (and REVIEW for unclear photos).

    pip install -r requirements.txt
    cp .env.example .env      # EBAY_CLIENT_ID / EBAY_CLIENT_SECRET (+ ANTHROPIC_API_KEY, NTFY_TOPIC optional)
    python -m pokescan.check # smoke test: keys, one live search per config entry, rate limit
    python -m pokescan.main  # run from the project root

Edit config/searches.yaml while running (applies in seconds; analysis.model needs a restart).
Set `analysis.enabled: true` to turn on Phase 2. Tests: python -m pytest tests

Inspect results:
    sqlite3 data/pokescan.db "select item_id, verdict, grade, created_at from assessments order by id desc limit 20"

## Free back-only analyzer (analysis.analyzer: classical)
OpenCV, no API cost. Finds the card, warps it flat, identifies the BACK (blue border ring), scores
whitening on 8 zones (4 edges, 4 corners). Verdicts: PASS ("BACK OK, now look at the front"),
FAIL (confident whitening), REVIEW (no back photo / can't localize / too much hidden by glare).
Glare never causes a FAIL: it is masked out, zones mostly hidden by it are "not checked",
and zone results are merged across all back photos.

Calibrate on real listings: set `analysis.classical.debug_dir: data/debug`, run a day, open
data/debug/<item>/<n>_zones.jpg (red = counted as whitish, cyan = glare, green = checked band,
numbers = whitish fraction per zone) and adjust `whitening_thresholds`.

## Windows
    py -3.12 -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    copy .env.example .env      (then edit .env)
    python -m pokescan.check
    python -m pokescan.main     (Ctrl+C to stop)
Always-on: Task Scheduler -> trigger "At log on", action `<project>\.venv\Scripts\python.exe -m pokescan.main`,
"Start in" = project folder, and enable "restart if the task fails". Disable sleep while plugged in.

## Searching by year (or set, or both)
`years: [1999, 2000]` on a search filters by eBay's "Year Manufactured" item-specific (global `year_match: aspect`),
or by keyword in the title (`year_match: title`). `sets:` still works and combines with years.
Each query walks up to `max_pages` pages newest-first and stops at the first listing already seen.
Run `python -m pokescan.check` to see the item-specific names/values eBay actually offers.
