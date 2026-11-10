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

    def __init__(self, loader, poller, budget, handlers: list[ListingHandler], tick_seconds: float = 5,
                 alert=None, alert_after: int = 3):
        self.loader, self.poller, self.budget = loader, poller, budget
        self.handlers, self.tick = handlers, tick_seconds
        self._last_run: dict[str, float] = {}
        self._stop = asyncio.Event()
        self.alert, self.alert_after = alert, alert_after      # alert: anything with async send(title, body, url=None)
        self._fails: dict[str, int] = {}
        self._alerted: set[str] = set()
        self._budget_alert_day: str | None = None

    async def _send_alert(self, title: str, body: str) -> None:
        if self.alert is None:
            return
        try:
            await self.alert.send(title, body)
        except Exception:
            log.exception("alert delivery failed")

    async def _record_failure(self, name: str, err: str) -> None:
        self._fails[name] = self._fails.get(name, 0) + 1
        if self._fails[name] >= self.alert_after and name not in self._alerted:
            self._alerted.add(name)   # one alert per outage, not one per poll
            await self._send_alert(f"pokescan: [{name}] failing", f"{self._fails[name]} polls in a row failed. Last error: {err[:300]}")

    async def _record_success(self, name: str) -> None:
        self._fails[name] = 0
        if name in self._alerted:
            self._alerted.discard(name)
            await self._send_alert(f"pokescan: [{name}] recovered", "Polling is working again.")

    def stop(self) -> None:
        self._stop.set()

    async def _poll_one(self, s, cfg) -> bool:
        """Poll one search and dispatch new listings to handlers. False = budget exhausted, stop for now."""
        try:
            for listing in await self.poller.poll(s, cfg.global_):
                for h in self.handlers:
                    await h.handle(listing)
        except BudgetExhausted as e:
            log.warning("%s - pausing polls until budget resets (UTC midnight)", e)
            from datetime import datetime, timezone
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._budget_alert_day != today:      # at most one budget alert per UTC day
                self._budget_alert_day = today
                await self._send_alert("pokescan: daily API budget used up", f"{e}. Polling resumes at UTC midnight.")
            return False
        except EbayError as e:
            log.error("[%s] eBay error: %s", s.name, e)
            await self._record_failure(s.name, str(e))
        except Exception as e:
            log.exception("[%s] unexpected error", s.name)
            await self._record_failure(s.name, repr(e))
        else:
            await self._record_success(s.name)
        return True

    async def run_once(self, only: str | None = None) -> None:
        """Manual mode: poll every enabled search (or just `only`) once, ignoring intervals, then return."""
        cfg = self.loader.current()
        self.budget.daily_limit = cfg.global_.daily_call_budget
        for s in cfg.all_searches():
            if not s.enabled or (only and s.name != only):
                continue
            if not await self._poll_one(s, cfg):
                break

    async def run(self) -> None:
        while not self._stop.is_set():
            cfg = self.loader.current()
            self.budget.daily_limit = cfg.global_.daily_call_budget
            now = time.monotonic()
            for s in cfg.all_searches():
                if not s.enabled:
                    continue
                interval = cfg.global_.intervals_minutes[s.priority] * 60
                if now - self._last_run.get(s.name, -1e9) < interval:
                    continue
                self._last_run[s.name] = now
                if not await self._poll_one(s, cfg):
                    break
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick)
            except asyncio.TimeoutError:
                pass
