import asyncio
from test_manual import Loader
from pokescan.ebay.errors import BudgetExhausted, EbayError
from pokescan.pipeline.budget import RateBudget
from pokescan.pipeline.scheduler import Scheduler
from pokescan.storage import db
from pokescan.storage.repositories import UsageRepository


class Sink:
    def __init__(self): self.sent = []
    async def send(self, title, body, url=None): self.sent.append(title)


class Flaky:
    def __init__(self, script): self.script = list(script)
    async def poll(self, search, g):
        step = self.script.pop(0) if self.script else None
        if step: raise step
        return []


def make(script, alert_after=3):
    sink = Sink()
    sched = Scheduler(Loader(), Flaky(script), RateBudget(UsageRepository(db.connect(":memory:")), 100), [],
                      alert=sink, alert_after=alert_after)
    return sched, sink


def run_polls(sched, n, name="ex-era"):
    for _ in range(n):
        asyncio.run(sched.run_once(name))


def test_one_alert_per_outage_then_recovery():
    sched, sink = make([EbayError("boom")] * 5 + [None])
    run_polls(sched, 6)
    assert sink.sent == ["pokescan: [ex-era] failing", "pokescan: [ex-era] recovered"]

def test_no_alert_below_threshold():
    sched, sink = make([EbayError("x"), EbayError("x"), None, EbayError("x")])
    run_polls(sched, 4)
    assert sink.sent == []                      # failures weren't consecutive enough

def test_budget_alert_once_per_day():
    sched, sink = make([BudgetExhausted("used up")] * 3)
    run_polls(sched, 3)
    assert sink.sent == ["pokescan: daily API budget used up"]
