from __future__ import annotations
import argparse, asyncio, logging, os, signal, sys
import httpx
from dotenv import load_dotenv

from pokescan.analysis.claude_vision import ClaudeVisionAnalyzer
from pokescan.analysis.handler import AnalysisHandler
from pokescan.config.loader import ConfigLoader
from pokescan.ebay.auth import EbayAuth
from pokescan.ebay.client import EbayBrowseClient
from pokescan.notify.handlers import ConsoleNotifier, NtfyNotifier, WatchNotifier
from pokescan.pipeline.budget import RateBudget
from pokescan.pipeline.poller import SearchPoller
from pokescan.pipeline.scheduler import Scheduler
from pokescan.storage import db
from pokescan.storage.image_store import ImageStore
from pokescan.storage.repositories import (AssessmentRepository, ListingRepository,
                                            CardDetailsRepository, NotificationRepository, PollRunRepository, UsageRepository)


async def amain(args: argparse.Namespace) -> None:
    load_dotenv()
    from logging.handlers import RotatingFileHandler
    log_path = os.getenv("POKESCAN_LOG", "data/pokescan.log")
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    logging.basicConfig(                       # console + rotating file (unattended runs have no console)
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")])

    loader = ConfigLoader(os.getenv("POKESCAN_CONFIG", "config/searches.yaml"))
    cfg = loader.current()
    conn = db.connect(os.getenv("POKESCAN_DB", "data/pokescan.db"))
    budget = RateBudget(UsageRepository(conn), cfg.global_.daily_call_budget)
    listings = ListingRepository(conn)

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as http:
        auth = EbayAuth(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], http)
        client = EbayBrowseClient(auth, http, budget, cfg.global_.marketplace)
        poller = SearchPoller(client, listings, PollRunRepository(conn), seed_silently=not args.no_seed)

        push = NtfyNotifier(os.environ["NTFY_TOPIC"], http) if os.getenv("NTFY_TOPIC") else ConsoleNotifier()
        handlers: list = [ConsoleNotifier(), WatchNotifier(push, loader)]  # console log of every new listing + watchlist pushes
        worker: asyncio.Task | None = None
        analysis = None

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
                listings, AssessmentRepository(conn), NotificationRepository(conn),
                cards=CardDetailsRepository(conn))
            handlers.append(analysis)
            worker = asyncio.create_task(analysis.run())
        elif os.getenv("NTFY_TOPIC"):
            handlers.append(push)  # Phase 1 behaviour: push every new listing

        sched = Scheduler(loader, poller, budget, handlers, alert=push)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, sched.stop)
            except NotImplementedError:   # Windows event loops don't support this; Ctrl+C is handled in __main__
                pass
        try:
            if args.once:
                await sched.run_once(args.search)
                if analysis:                       # let queued photo analysis finish before exiting
                    print(f"Waiting for {analysis.queue.qsize()} queued listing(s) to be analyzed...")
                    await analysis.queue.join()
                print("Done (--once).")
            else:
                await sched.run()
        finally:
            if worker:
                worker.cancel()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):   # Windows consoles default to a legacy codepage; titles can contain Japanese/emoji
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="pokescan", description="eBay Pokemon card listing monitor")
    ap.add_argument("--once", action="store_true", help="poll every enabled search once, then exit (no scheduler loop)")
    ap.add_argument("--search", metavar="NAME", help="with --once: only run this search")
    ap.add_argument("--no-seed", action="store_true",
                    help="only matters when lookback_days is 0: don't silently seed a search's first run")
    args = ap.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("Stopped.")
