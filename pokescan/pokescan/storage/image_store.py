from __future__ import annotations
import logging
from pathlib import Path
import httpx
from pokescan.ebay.query import full_res

log = logging.getLogger(__name__)


class ImageStore:
    """Downloads listing photos to data/images/<item_id>/<n>.<ext>.
    Image CDN fetches do NOT count against the eBay API budget."""

    def __init__(self, http: httpx.AsyncClient, root: str | Path = "data/images"):
        self.http, self.root = http, Path(root)

    async def download(self, item_id: str, urls: list[str]) -> list[Path | None]:
        """Returns a list aligned with urls (None where a download failed)."""
        safe = item_id.replace("|", "_").replace("/", "_")
        folder = self.root / safe
        folder.mkdir(parents=True, exist_ok=True)
        out: list[Path | None] = []
        for i, url in enumerate(urls):
            saved = None
            for candidate in dict.fromkeys([full_res(url), url]):  # try 1600px, fall back to original
                try:
                    r = await self.http.get(candidate, follow_redirects=True)
                except httpx.HTTPError as e:
                    log.warning("image fetch error %s: %s", candidate, e)
                    continue
                ct = r.headers.get("content-type", "")
                if r.status_code == 200 and r.content and ct.startswith("image/"):
                    ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
                    saved = folder / f"{i}{ext}"
                    saved.write_bytes(r.content)
                    break
            out.append(saved)
        return out
