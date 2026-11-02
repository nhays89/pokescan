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


G = GlobalConfig(page_size=2, max_pages=3)
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
