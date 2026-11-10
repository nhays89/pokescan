"""One-off lookup of a specific card:  python -m pokescan.search "dark golduck" --max-price 300
Prints the newest matches straight from eBay. Writes nothing to the database, never triggers analysis,
but DOES count against your daily API budget (1 call). Category, language and any global `aspects`
from config/searches.yaml still apply; years/sets/lookback do not."""
from __future__ import annotations
import argparse, asyncio, os, sys
from datetime import datetime, timedelta, timezone
import httpx
from dotenv import load_dotenv
from pokescan.config.loader import ConfigLoader
from pokescan.config.models import FilterConfig, SearchConfig
from pokescan.ebay.auth import EbayAuth
from pokescan.ebay.client import EbayBrowseClient
from pokescan.ebay.errors import EbayError
from pokescan.ebay.query import build_params
from pokescan.pipeline.budget import RateBudget
from pokescan.pipeline.filters import passes
from pokescan.storage import db
from pokescan.storage.repositories import UsageRepository


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="pokescan.search", description="One-off eBay lookup of a specific card")
    ap.add_argument("query", help='search text, e.g. "dark golduck" (wrap in extra quotes for an exact phrase)')
    ap.add_argument("--min-price", type=float)
    ap.add_argument("--max-price", type=float)
    ap.add_argument("--limit", type=int, default=50, help="how many newest results to fetch (max 200)")
    ap.add_argument("--days", type=int, default=0, help="only show listings created in the last N days (0 = any age)")
    ap.add_argument("--exclude", nargs="*", default=[], help="drop titles containing any of these words")
    ap.add_argument("--dry-run", action="store_true", help="print the request only; no keys or network needed")
    return ap.parse_args(argv)


def make_search(args: argparse.Namespace) -> SearchConfig:
    return SearchConfig(name="manual", q=args.query, exclude_terms=args.exclude,
                        filters=FilterConfig(price_min=args.min_price, price_max=args.max_price))


async def run(args: argparse.Namespace) -> int:
    load_dotenv()
    cfg = ConfigLoader(os.getenv("POKESCAN_CONFIG", "config/searches.yaml")).current()
    search = make_search(args)
    params = build_params(search, cfg.global_)
    params["limit"] = str(min(max(args.limit, 1), 200))
    if args.dry_run:
        for k, v in params.items():
            print(f"  {k:13} = {v}")
        return 0
    for k in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"):
        if not os.getenv(k):
            print(f"{k} missing - see .env.example"); return 1
    conn = db.connect(os.getenv("POKESCAN_DB", "data/pokescan.db"))
    budget = RateBudget(UsageRepository(conn), cfg.global_.daily_call_budget)
    async with httpx.AsyncClient(timeout=20) as http:
        auth = EbayAuth(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], http)
        try:
            page = await EbayBrowseClient(auth, http, budget, cfg.global_.marketplace).search(params)
        except EbayError as e:
            print(f"eBay error: {e}"); return 1
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days) if args.days > 0 else None
    shown = 0
    print(f"{page.total} active listing(s) match; newest first:\n")
    for it in page.items:
        if not passes(it, search):
            continue
        created = (it.itemCreationDate or "")[:10]
        if cutoff and it.itemCreationDate:
            try:
                if datetime.fromisoformat(it.itemCreationDate.replace("Z", "+00:00")) < cutoff:
                    continue
            except ValueError:
                pass
        price = f"{it.price.value:.2f} {it.price.currency}" if it.price else "n/a"
        print(f"{price:>14}  {created or 'no date':10}  {it.title[:80]}\n{'':28}{it.itemWebUrl}")
        shown += 1
    print(f"\n{shown} shown.")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(run(parse_args())))
