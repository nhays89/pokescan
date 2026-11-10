from __future__ import annotations
import asyncio, logging
import httpx
from .auth import EbayAuth
from .errors import EbayError, RateLimited
from .models import ItemSummary, SearchPage

log = logging.getLogger(__name__)
BASE = "https://api.ebay.com/buy/browse/v1"


class EbayBrowseClient:
    def __init__(self, auth: EbayAuth, http: httpx.AsyncClient, budget, marketplace: str = "EBAY_US"):
        self._auth, self._http, self._budget, self._mkt = auth, http, budget, marketplace

    async def _get(self, path: str, params: dict | None = None) -> dict:
        reauthed = False
        for attempt in range(4):
            self._budget.consume()  # raises BudgetExhausted before we ever exceed the cap
            token = await self._auth.get_token()
            try:
                r = await self._http.get(
                    f"{BASE}{path}", params=params,
                    headers={"Authorization": f"Bearer {token}",
                             "X-EBAY-C-MARKETPLACE-ID": self._mkt},
                )
            except httpx.TransportError as e:
                log.warning("Network error (%s), retry %d", e, attempt)
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401 and not reauthed:
                self._auth.invalidate(); reauthed = True
                continue
            if r.status_code == 429:
                raise RateLimited(r.text[:200])
            if r.status_code >= 500:
                await asyncio.sleep(2 ** attempt)
                continue
            raise EbayError(f"{r.status_code}: {r.text[:300]}")
        raise EbayError(f"Gave up on {path} after retries")

    async def search(self, params: dict[str, str]) -> SearchPage:
        body = await self._get("/item_summary/search", params)
        items = []
        for raw in body.get("itemSummaries", []):
            item = ItemSummary.model_validate(raw)
            item.raw = raw
            items.append(item)
        return SearchPage(items=items, total=body.get("total", 0), next_url=body.get("next"),
                          refinement=body.get("refinement", {}))

    async def get_item(self, item_id: str) -> dict:
        """Full item detail (all photos live here). Costs 1 call; use sparingly."""
        return await self._get(f"/item/{item_id}")
