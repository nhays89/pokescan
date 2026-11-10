import asyncio
from pokescan.config.models import GlobalConfig, SearchConfig
from pokescan.ebay.models import ItemSummary, SearchPage
from pokescan.pipeline.poller import SearchPoller
from pokescan.storage import db
from pokescan.storage.repositories import ListingRepository, PollRunRepository


def item(i):
    raw = {"itemId": f"v1|{i}|0", "title": f"Charizard {i}", "price": {"value": "50", "currency": "USD"}}
    it = ItemSummary.model_validate(raw); it.raw = raw
    return it


class FakeClient:
    def __init__(self, pages):          # pages: list of lists of ids; beyond the end -> always "new" items
        self.pages, self.calls = pages, 0
    async def search(self, params):
        n = int(params["offset"]) // int(params["limit"])
        self.calls += 1
        ids = self.pages[n] if n < len(self.pages) else [1000 + n * 10 + k for k in range(2)]
        return SearchPage(items=[item(i) for i in ids], total=99, next_url="more")


def setup(pages, preseed=()):
    conn = db.connect(":memory:")
    repo = ListingRepository(conn)
    for i in preseed:
        repo.upsert("s", item(i))
    return SearchPoller(FakeClient(pages), repo, PollRunRepository(conn)), conn


G = GlobalConfig(page_size=2, max_pages=3, lookback_days=0)   # seed-mode semantics
S = SearchConfig(name="s")


def test_stops_at_first_seen_listing():
    poller, conn = setup([[1, 2], [3, 99]], preseed=[99])
    fresh = asyncio.run(poller.poll(S, G))
    assert [l.item_id for l in fresh] == ["v1|1|0", "v1|2|0", "v1|3|0"]
    assert poller.client.calls == 2                       # page 2 had an old item -> stop
    assert conn.execute("SELECT calls FROM poll_runs").fetchone()[0] == 2


def test_max_pages_bounds_the_walk():
    poller, _ = setup([], preseed=[99])                   # never meets an old item
    asyncio.run(poller.poll(S, G))
    assert poller.client.calls == 3


def test_short_page_ends_paging():
    poller, _ = setup([[1], ], preseed=[99])              # fewer items than page_size -> last page
    asyncio.run(poller.poll(S, G))
    assert poller.client.calls == 1


def test_first_run_seeds_silently():
    poller, conn = setup([[1, 2], [3, 4], [5, 6]])
    assert asyncio.run(poller.poll(S, G)) == []
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 6


# ---------------- lookback mode ----------------
from datetime import datetime, timedelta, timezone

def iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def dated(i, days_ago):
    raw = {"itemId": f"v1|{i}|0", "title": f"Charizard {i}", "price": {"value": "50", "currency": "USD"}}
    if days_ago is not None:
        raw["itemCreationDate"] = iso(days_ago)
    it = ItemSummary.model_validate(raw); it.raw = raw
    return it

class DatedClient:
    """pages: list of [(id, days_ago)]; past the end it yields endless fresh in-window items."""
    def __init__(self, pages):
        self.pages, self.calls = pages, 0
    async def search(self, params):
        n = int(params["offset"]) // int(params["limit"]); self.calls += 1
        rows = self.pages[n] if n < len(self.pages) else [(5000 + n * 10 + k, 1) for k in range(int(params["limit"]))]
        return SearchPage(items=[dated(i, d) for i, d in rows], total=99, next_url="more")

GL = GlobalConfig(page_size=3, max_pages=3, backfill_max_pages=5, lookback_days=10)

def dsetup(pages, preseed=()):
    conn = db.connect(":memory:")
    repo = ListingRepository(conn)
    for i in preseed:
        repo.upsert("s", dated(i, 1))
    return SearchPoller(DatedClient(pages), repo, PollRunRepository(conn)), conn

def test_lookback_reports_recent_on_first_run_and_ignores_old():
    poller, conn = dsetup([[(1, 1), (2, 5), (3, 9)], [(4, 12), (5, 20), (6, 30)]])
    fresh = asyncio.run(poller.poll(S, GL))
    assert [l.item_id for l in fresh] == ["v1|1|0", "v1|2|0", "v1|3|0"]     # no silent seeding
    assert poller.client.calls == 2                                         # stopped at the first too-old page
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 3   # old ones never stored

def test_listing_without_a_date_is_not_dropped():
    poller, _ = dsetup([[(1, None)]])
    assert len(asyncio.run(poller.poll(S, GL))) == 1

def test_first_run_uses_backfill_pages_later_runs_use_max_pages():
    poller, _ = dsetup([])                                   # endless fresh in-window items, no history
    asyncio.run(poller.poll(S, GL)); assert poller.client.calls == 5
    poller2, _ = dsetup([], preseed=[99])                    # has history
    asyncio.run(poller2.poll(S, GL)); assert poller2.client.calls == 3

def test_per_search_override_can_disable_window():
    sc = SearchConfig(name="s", lookback_days=0)
    poller, _ = dsetup([[(1, 400)]])
    assert asyncio.run(poller.poll(sc, GL)) == []            # lookback off -> seed mode -> silent first run


def test_warns_when_page_cap_hit_without_overlap(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    poller, _ = dsetup([], preseed=[99])        # endless fresh items, has history -> never sees an old one
    asyncio.run(poller.poll(S, GL))
    assert any("MISSED" in r.message for r in caplog.records)

def test_no_warning_when_overlap_found(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    poller, _ = dsetup([[(1, 1), (2, 1), (99, 1)]], preseed=[99])
    asyncio.run(poller.poll(S, GL))
    assert not caplog.records
