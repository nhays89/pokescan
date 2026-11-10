# Decisions (and why)

- **Python worker, not a web server.** Glue-heavy, image/vision libraries are first-class, nothing needs inbound requests. Add FastAPI only if a dashboard is wanted.
- **Polling, not push.** eBay offers no buyer-side push for new listings; sort `newlyListed`, page until overlap.
- **SQLite (WAL).** Single writer, single user, zero ops. SQLAlchemy not used; thin repositories over `sqlite3`.
- **YAML config, hot-reloaded, validated by pydantic.** Bad edit keeps last good config. Secrets only in `.env`.
- **Rate limit is per app keyset, not per IP.** Local hosting does not buy headroom. Budget is persisted and enforced in the client.
- **Lookback window instead of seeding.** Seeding silently stored all current listings. A date window (`lookback_days`, default 1 in the sample) reports what is new, stops paging at the cutoff, and never stores old listings. `lookback_days: 0` restores seeding. Gap risk: downtime longer than the window loses listings.
- **Filter by year via eBay item specific by default (`year_match: aspect`).** Precise but drops listings with the field blank; `title` mode is the fallback.
- **Language handling is two-layer.** Cheap title auto-fail (japanese/jp/...) before any API spend, then the `Language` item specific from `getItem`: set and not English => FAIL; blank passes. We do not use the API-level language filter by default because it drops blanks.
- **Classical (OpenCV) first, back-only.** Free, explainable, and whitening on the dark-blue back is the strongest classical signal. Vision model is optional (`analysis.analyzer: claude`) and could become a second stage for fronts.
- **Glare policy.** Glare is never grounds for rejection; mask it, mark zones unassessed, merge across photos, and fall back to REVIEW. FAIL needs confident whitening outside glare.
- **Errors fall toward REVIEW.** A false clear wastes a few seconds of looking; a false reject silently loses a card.
- **Watchlist is a separate config section.** Different intent (specific cards, per-card price caps, always notify), different query (title, no year, no `pokemon` keyword so titles lacking the word are not missed), different cost profile (1 call/card/poll).
- **Manual `search` command is not in the YAML.** It is a throwaway question, not a standing job; it reads global settings only.
- **Auto-fail Japanese.** Title terms and the Language aspect; rule fails are stored with `model='rule'` so they can be audited.
- **Ports and adapters.** `ConditionAnalyzer`, `ListingHandler`, `Notifier` are Protocols; `pokescan/main.py` is the composition root. New analyzers/notifiers plug in without touching the poller.
