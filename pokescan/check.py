"""Setup smoke test: python -m pokescan.check
Verifies keys, runs ONE small live search per configured search (1 API call each), shows what eBay
returns, and tries to read your real rate limit. Does not write listings to the database."""
from __future__ import annotations
import argparse, asyncio, os, sys
from urllib.parse import quote, urlencode
from collections import Counter
import httpx
from dotenv import load_dotenv
from pokescan.config.loader import ConfigLoader
from pokescan.ebay.auth import EbayAuth
from pokescan.ebay.client import EbayBrowseClient
from pokescan.ebay.errors import EbayError
from pokescan.ebay.query import build_params, expand_queries

SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
RATE_URL = "https://api.ebay.com/developer/analytics/v1_beta/rate_limit/"


class _NoBudget:          # check runs aren't counted against the daily counter
    def consume(self): ...


def planned_requests(cfg):
    """Every search request a poll would send, as (search_name, query, params, full_url). Pure: makes no calls."""
    out = []
    for s in cfg.all_searches():
        if not s.enabled:
            continue
        for q in expand_queries(s, cfg.global_):
            params = build_params(s, cfg.global_, q=q)
            out.append((s.name, q, params, f"{SEARCH_URL}?{urlencode(params, quote_via=quote)}"))
    return out


def print_plan(cfg) -> None:
    plan = planned_requests(cfg)
    g = cfg.global_
    print(f"{len(plan)} request(s) per poll cycle (page 1 each). Headers on every call:\n"
          f"    Authorization: Bearer <token>\n    X-EBAY-C-MARKETPLACE-ID: {g.marketplace}\n")
    for name, q, params, url in plan:
        print(f"[{name}]  q = {q!r}")
        for k, v in params.items():
            print(f"    {k:13} = {v}")
        print(f"    URL: {url}\n")
    days = {s.name: (g.lookback_days if s.lookback_days is None else s.lookback_days) for s in cfg.all_searches() if s.enabled}
    print("Applied client-side after results arrive (not part of the request):")
    print(f"    lookback window (days): {days}")
    for s in cfg.all_searches():
        if s.enabled and s.exclude_terms:
            print(f"    [{s.name}] drops titles containing: {', '.join(s.exclude_terms)}")
    est_search = est_watch = 0.0
    for s in cfg.all_searches():
        if s.enabled:
            calls = len(expand_queries(s, g)) * 1440 / g.intervals_minutes[s.priority]
            if s.name.startswith("watch:"):
                est_watch += calls
            else:
                est_search += calls
    print(f"Estimated steady-state polling cost (page 1 only): ~{est_search + est_watch:.0f} calls/day "
          f"[{est_search:.0f} searches + {est_watch:.0f} watchlist] of a {g.daily_call_budget}/day budget.")
    print("    Busy polls add extra pages; each analyzed listing adds 1 getItem call "
          f"(capped at analysis.max_analyses_per_day = {cfg.analysis.max_analyses_per_day}).")
    print(f"Paging: up to {g.max_pages} pages x {g.page_size} per query per poll "
          f"(first run: up to {g.backfill_max_pages}); each page = 1 call.")


async def main(dry_run: bool = False) -> int:
    load_dotenv()
    if dry_run:
        print_plan(ConfigLoader(os.getenv("POKESCAN_CONFIG", "config/searches.yaml")).current())
        return 0
    for k in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"):
        if not os.getenv(k):
            print(f"FAIL: {k} missing. Copy .env.example to .env and fill it in."); return 1
    cfg = ConfigLoader(os.getenv("POKESCAN_CONFIG", "config/searches.yaml")).current()
    print(f"Config OK: {len(cfg.all_searches())} searches, analysis={'on' if cfg.analysis.enabled else 'off'}")

    async with httpx.AsyncClient(timeout=20) as http:
        auth = EbayAuth(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], http)
        try:
            token = await auth.get_token()
        except EbayError as e:
            print(f"FAIL: token request rejected -> {e}\n  Use PRODUCTION keys (not Sandbox) and the App ID / Cert ID pair."); return 1
        print("Token OK")

        client = EbayBrowseClient(auth, http, _NoBudget(), cfg.global_.marketplace)
        cats: Counter = Counter()
        for s in cfg.all_searches():
            if not s.enabled:
                continue
            q = expand_queries(s, cfg.global_)[0]
            params = build_params(s, cfg.global_, q=q); params["limit"] = "20"
            try:
                page = await client.search(params)
            except EbayError as e:
                print(f"FAIL [{s.name}] {e}\n  params sent: {params}"); continue
            print(f"[{s.name}] {page.total} total matches; first query: {q!r}")
            for it in page.items[:3]:
                price = f"{it.price.value:.2f} {it.price.currency}" if it.price else "n/a"
                print(f"    {price:>12}  {(it.itemCreationDate or 'no date')[:10]}  {it.title[:60]}")
            for it in page.items:
                for c in it.raw.get("categories", []):
                    cats[(c.get("categoryId"), c.get("categoryName"))] += 1
            if not page.items:
                print("    (no results - loosen filters/set names, or check the category id)")

        if cats:
            print("Categories seen (confirm your category_ids):")
            for (cid, name), n in cats.most_common(5):
                print(f"    {cid}  {name}  x{n}")

        first = next((x for x in cfg.all_searches() if x.enabled), None)
        if first:   # 1 extra call: ask eBay which item-specific names/values exist, so filters use real names
            probe = build_params(first, cfg.global_, q=expand_queries(first, cfg.global_)[0])
            probe.pop("aspect_filter", None); probe["limit"] = "1"; probe["fieldgroups"] = "ASPECT_REFINEMENTS"
            try:
                page = await client.search(probe)
                print("Item specifics eBay offers for this search (use these exact names in aspect filters):")
                for dist in page.refinement.get("aspectDistributions", []):
                    name = dist.get("localizedAspectName", "")
                    if any(k in name.lower() for k in ("year", "language", "set", "era", "game")):
                        vals = ", ".join(f"{v.get('localizedAspectValueName')} ({v.get('matchCount')})"
                                         for v in dist.get("aspectValueDistributions", [])[:8])
                        print(f"    {name}: {vals}")
            except EbayError as e:
                print(f"(aspect probe failed: {e})")

        try:
            r = await http.get(RATE_URL, params={"api_context": "buy", "api_name": "browse"},
                               headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                for ctx in r.json().get("rateLimits", []):
                    for res in ctx.get("resources", []):
                        for rate in res.get("rates", []):
                            print(f"Rate limit [{res.get('name')}]: {rate.get('count')} used / {rate.get('limit')} per {rate.get('timeDesc')}")
            else:
                print(f"(could not read rate limit: HTTP {r.status_code}; check Developer Portal > Analytics instead)")
        except httpx.HTTPError as e:
            print(f"(rate limit lookup skipped: {e})")
    print("Done. If this looked right: python -m pokescan.main")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="pokescan.check")
    ap.add_argument("--dry-run", action="store_true", help="print the exact requests that would be sent; no keys or network needed")
    sys.exit(asyncio.run(main(ap.parse_args().dry_run)))
