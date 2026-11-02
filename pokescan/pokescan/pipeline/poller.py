from __future__ import annotations
import logging
from pokescan.config.models import GlobalConfig, SearchConfig
from pokescan.domain.models import Listing
from pokescan.ebay.query import build_params, expand_queries
from . import filters

log = logging.getLogger(__name__)


class SearchPoller:
    def __init__(self, client, listings, runs):
        self.client, self.listings, self.runs = client, listings, runs

    async def poll(self, search: SearchConfig, g: GlobalConfig) -> list[Listing]:
        """Walk each query newest-first, up to g.max_pages pages, stopping as soon as a page contains
        a listing we already had (everything after it is older). Returns only NEW listings.
        On a search's very first run we seed the DB silently so you aren't spammed with the
        entire current inventory."""
        seed = not self.listings.search_has_history(search.name)
        fetched = new = calls = 0
        new_ids: set[str] = set()
        try:
            fresh: list[Listing] = []
            for q in expand_queries(search, g):
                for page_no in range(g.max_pages):
                    page = await self.client.search(build_params(search, g, offset=page_no * g.page_size, q=q))
                    calls += 1
                    saw_old = False
                    for item in page.items:
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
                    if saw_old or len(page.items) < g.page_size or not page.next_url:
                        break
            self.runs.record(search.name, calls, fetched, new)
            if seed:
                log.info("[%s] seeded %d existing listings", search.name, fetched)
                return []
            return fresh
        except Exception as e:
            self.runs.record(search.name, calls, fetched, new, error=repr(e))
            raise
