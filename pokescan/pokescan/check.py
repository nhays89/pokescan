"""Setup smoke test: python -m pokescan.check
Verifies keys, runs ONE small live search per configured search (1 API call each), shows what eBay
returns, and tries to read your real rate limit. Does not write listings to the database."""
from __future__ import annotations
import asyncio, os, sys
from collections import Counter
import httpx
from dotenv import load_dotenv
from pokescan.config.loader import ConfigLoader
from pokescan.ebay.auth import EbayAuth
from pokescan.ebay.client import EbayBrowseClient
from pokescan.ebay.errors import EbayError
from pokescan.ebay.query import build_params, expand_queries

RATE_URL = "https://api.ebay.com/developer/analytics/v1_beta/rate_limit/"


class _NoBudget:          # check runs aren't counted against the daily counter
    def consume(self): ...


async def main() -> int:
    load_dotenv()
    for k in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"):
        if not os.getenv(k):
            print(f"FAIL: {k} missing. Copy .env.example to .env and fill it in."); return 1
    cfg = ConfigLoader(os.getenv("POKESCAN_CONFIG", "config/searches.yaml")).current()
    print(f"Config OK: {len(cfg.searches)} searches, analysis={'on' if cfg.analysis.enabled else 'off'}")

    async with httpx.AsyncClient(timeout=20) as http:
        auth = EbayAuth(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], http)
        try:
            token = await auth.get_token()
        except EbayError as e:
            print(f"FAIL: token request rejected -> {e}\n  Use PRODUCTION keys (not Sandbox) and the App ID / Cert ID pair."); return 1
        print("Token OK")

        client = EbayBrowseClient(auth, http, _NoBudget(), cfg.global_.marketplace)
        cats: Counter = Counter()
        for s in cfg.searches:
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
                print(f"    {price:>12}  {it.title[:70]}")
            for it in page.items:
                for c in it.raw.get("categories", []):
                    cats[(c.get("categoryId"), c.get("categoryName"))] += 1
            if not page.items:
                print("    (no results - loosen filters/set names, or check the category id)")

        if cats:
            print("Categories seen (confirm your category_ids):")
            for (cid, name), n in cats.most_common(5):
                print(f"    {cid}  {name}  x{n}")

        first = next((x for x in cfg.searches if x.enabled), None)
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
    sys.exit(asyncio.run(main()))
