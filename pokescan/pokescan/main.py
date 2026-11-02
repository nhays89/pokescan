from __future__ import annotations
import asyncio, logging, os, signal, sys
import httpx
from dotenv import load_dotenv

from pokescan.analysis.claude_vision import ClaudeVisionAnalyzer
from pokescan.analysis.handler import AnalysisHandler
from pokescan.config.loader import ConfigLoader
from pokescan.ebay.auth import EbayAuth
from pokescan.ebay.client import EbayBrowseClient
from pokescan.notify.handlers import ConsoleNotifier, NtfyNotifier
from pokescan.pipeline.budget import RateBudget
from pokescan.pipeline.poller import SearchPoller
from pokescan.pipeline.scheduler import Scheduler
from pokescan.storage import db
from pokescan.storage.image_store import ImageStore
from pokescan.storage.repositories import (AssessmentRepository, ListingRepository,
                                            NotificationRepository, PollRunRepository, UsageRepository)


async def amain() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    loader = ConfigLoader(os.getenv("POKESCAN_CONFIG", "config/searches.yaml"))
    cfg = loader.current()
    conn = db.connect(os.getenv("POKESCAN_DB", "data/pokescan.db"))
    budget = RateBudget(UsageRepository(conn), cfg.global_.daily_call_budget)
    listings = ListingRepository(conn)

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as http:
        auth = EbayAuth(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], http)
        client = EbayBrowseClient(auth, http, budget, cfg.global_.marketplace)
        poller = SearchPoller(client, listings, PollRunRepository(conn))

        push = NtfyNotifier(os.environ["NTFY_TOPIC"], http) if os.getenv("NTFY_TOPIC") else ConsoleNotifier()
        handlers: list = [ConsoleNotifier()]  # logs every new listing to the console
        worker: asyncio.Task | None = None

        if cfg.analysis.enabled:
            if cfg.analysis.analyzer == "classical":
                from pokescan.analysis.classical import ClassicalAnalyzer  # lazy: needs opencv
                analyzer = ClassicalAnalyzer(lambda: loader.current().analysis.classical)  # hot-reloadable
            else:
                if not os.getenv("ANTHROPIC_API_KEY"):
                    raise SystemExit("analyzer is 'claude' but ANTHROPIC_API_KEY is not set")
                analyzer = ClaudeVisionAnalyzer(cfg.analysis.model)
            analysis = AnalysisHandler(
                loader, client, ImageStore(http), analyzer, push,
                listings, AssessmentRepository(conn), NotificationRepository(conn))
            handlers.append(analysis)
            worker = asyncio.create_task(analysis.run())
        elif os.getenv("NTFY_TOPIC"):
            handlers.append(push)  # Phase 1 behaviour: push every new listing

        sched = Scheduler(loader, poller, budget, handlers)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, sched.stop)
            except NotImplementedError:   # Windows event loops don't support this; Ctrl+C is handled in __main__
                pass
        try:
            await sched.run()
        finally:
            if worker:
                worker.cancel()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):   # Windows consoles default to a legacy codepage; titles can contain Japanese/emoji
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("Stopped.")
