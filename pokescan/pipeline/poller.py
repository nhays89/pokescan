from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from pokescan.config.models import GlobalConfig, SearchConfig
from pokescan.domain.models import Listing
from pokescan.ebay.query import build_params, expand_queries
from . import filters

log = logging.getLogger(__name__)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class SearchPoller:
    def __init__(self, client, listings, runs, seed_silently: bool = True):
        self.client, self.listings, self.runs, self.seed_silently = client, listings, runs, seed_silently

    @staticmethod
    def cutoff(search: SearchConfig, g: GlobalConfig) -> datetime | None:
        days = g.lookback_days if search.lookback_days is None else search.lookback_days
        return datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None

    async def poll(self, search: SearchConfig, g: GlobalConfig) -> list[Listing]:
        """Walk each query newest-first and return only NEW listings.

        Lookback mode (lookback_days > 0, the default): listings created before the cutoff are ignored
        (never stored), paging stops as soon as it reaches them, and there is NO silent seeding: whatever
        is new inside the window is reported/analyzed, including on the first run.
        Seed mode (lookback_days = 0): a search's first run stores current listings silently instead.
        In both modes paging also stops at the first listing already in the database."""
        has_history = self.listings.search_has_history(search.name)
        cutoff = self.cutoff(search, g)
        seed = cutoff is None and self.seed_silently and not has_history and not search.notify_existing
        max_pages = g.backfill_max_pages if (cutoff and not has_history) else g.max_pages
        fetched = new = calls = skipped_old = 0
        new_ids: set[str] = set()
        try:
            fresh: list[Listing] = []
            for q in expand_queries(search, g):
                for page_no in range(max_pages):
                    page = await self.client.search(build_params(search, g, offset=page_no * g.page_size, q=q))
                    calls += 1
                    saw_old = too_old = False
                    for item in page.items:
                        created = _parse(item.itemCreationDate)
                        if cutoff and created and created < cutoff:
                            too_old = True       # sorted newest-first: everything after this is older still
                            skipped_old += 1
                            continue
                        if not filters.passes(item, search):
                            continue
                        fetched += 1
                        listing, is_new = self.listings.upsert(search.name, item)
                        if is_new:
                            new += 1
                            new_ids.add(item.itemId)
                            fresh.append(listing)
                        elif item.itemId not in new_ids:   # genuinely seen before this poll
                            saw_old = True
                    if saw_old or too_old or len(page.items) < g.page_size or not page.next_url:
                        break
                else:
                    # Walked every allowed page and never reached an already-seen or too-old listing.
                    if cutoff and not has_history:
                        log.warning("[%s] first run stopped at backfill_max_pages (%d pages x %d) before reaching the "
                                    "lookback cutoff: older listings inside the window were NOT fetched. Narrow the "
                                    "query (price floor, fewer years) or raise backfill_max_pages.",
                                    search.name, max_pages, g.page_size)
                    else:
                        log.warning("[%s] hit max_pages (%d x %d) without reaching a listing seen before: more new "
                                    "listings arrived since the last poll than we can page through, so some were MISSED. "
                                    "Raise page_size/max_pages, poll more often, or narrow the query.",
                                    search.name, max_pages, g.page_size)
            self.runs.record(search.name, calls, fetched, new)
            if cutoff and not has_history:
                log.info("[%s] first run: %d new listing(s) in the last %d days (%d older ignored)",
                         search.name, new, (g.lookback_days if search.lookback_days is None else search.lookback_days), skipped_old)
            if seed:
                log.info("[%s] seeded %d existing listings", search.name, fetched)
                return []
            return fresh
        except Exception as e:
            self.runs.record(search.name, calls, fetched, new, error=repr(e))
            raise
