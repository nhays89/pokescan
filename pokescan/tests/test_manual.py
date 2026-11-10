import asyncio, yaml, pathlib
from pokescan.config.models import AppConfig
from pokescan.pipeline.scheduler import Scheduler
from pokescan.pipeline.budget import RateBudget
from pokescan.storage import db
from pokescan.storage.repositories import UsageRepository, ListingRepository, PollRunRepository
from pokescan.pipeline.poller import SearchPoller
from test_poller import FakeClient, item, S, G


class Loader:
    def __init__(self):
        d = yaml.safe_load(pathlib.Path("config/searches.yaml").read_text())
        self.cfg = AppConfig.model_validate(d)
    def current(self): return self.cfg


class Spy:
    def __init__(self): self.names = []
    async def poll(self, search, g): self.names.append(search.name); return []


def test_run_once_polls_each_enabled_search_once_and_returns():
    spy = Spy()
    sched = Scheduler(Loader(), spy, RateBudget(UsageRepository(db.connect(":memory:")), 100), [])
    asyncio.run(asyncio.wait_for(sched.run_once(), 5))
    enabled = [s.name for s in Loader().cfg.searches if s.enabled]
    assert spy.names == enabled

def test_run_once_single_search():
    spy = Spy()
    sched = Scheduler(Loader(), spy, RateBudget(UsageRepository(db.connect(":memory:")), 100), [])
    asyncio.run(sched.run_once("ex-era"))
    assert spy.names == ["ex-era"]

def test_no_seed_returns_first_run_listings():
    conn = db.connect(":memory:")
    poller = SearchPoller(FakeClient([[1, 2], [3]]), ListingRepository(conn), PollRunRepository(conn), seed_silently=False)
    assert len(asyncio.run(poller.poll(S, G))) == 3
