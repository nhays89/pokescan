from __future__ import annotations
import asyncio, logging, time
from typing import Protocol
from pokescan.domain.models import Listing
from pokescan.ebay.errors import BudgetExhausted, EbayError

log = logging.getLogger(__name__)


class ListingHandler(Protocol):
    async def handle(self, listing: Listing) -> None: ...


class Scheduler:
    """Ticks every few seconds; runs each enabled search when its priority interval elapses.
    Config is re-read every tick, so YAML edits apply within seconds."""

    def __init__(self, loader, poller, budget, handlers: list[ListingHandler], tick_seconds: float = 5):
        self.loader, self.poller, self.budget = loader, poller, budget
        self.handlers, self.tick = handlers, tick_seconds
        self._last_run: dict[str, float] = {}
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            cfg = self.loader.current()
            self.budget.daily_limit = cfg.global_.daily_call_budget
            now = time.monotonic()
            for s in cfg.searches:
                if not s.enabled:
                    continue
                interval = cfg.global_.intervals_minutes[s.priority] * 60
                if now - self._last_run.get(s.name, -1e9) < interval:
                    continue
                self._last_run[s.name] = now
                try:
                    for listing in await self.poller.poll(s, cfg.global_):
                        for h in self.handlers:
                            await h.handle(listing)
                except BudgetExhausted as e:
                    log.warning("%s - pausing polls until budget resets (UTC midnight)", e)
                    break
                except EbayError as e:
                    log.error("[%s] eBay error: %s", s.name, e)
                except Exception:
                    log.exception("[%s] unexpected error", s.name)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick)
            except asyncio.TimeoutError:
                pass
