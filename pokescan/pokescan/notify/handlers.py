import logging
import httpx
from pokescan.domain.models import Listing

log = logging.getLogger(__name__)


def _fmt(l: Listing) -> str:
    price = f"{l.price:.2f} {l.currency}" if l.price is not None else "n/a"
    return f"[{l.search_name}] {l.title} - {price}\n{l.url}"


class ConsoleNotifier:
    async def handle(self, listing: Listing) -> None:      # ListingHandler (every new listing)
        print("NEW:", _fmt(listing), flush=True)

    async def send(self, title: str, body: str, url: str | None = None) -> None:  # Notifier
        print(f"\n== {title} ==\n{body}\n", flush=True)


class NtfyNotifier:
    """Phone push via ntfy.sh (or your self-hosted server)."""

    def __init__(self, topic: str, http: httpx.AsyncClient, server: str = "https://ntfy.sh"):
        self.url, self.http = f"{server}/{topic}", http

    async def send(self, title: str, body: str, url: str | None = None) -> None:
        headers = {"Title": title.encode("ascii", "ignore").decode()[:100] or "pokescan"}
        if url:
            headers["Click"] = url
        try:
            await self.http.post(self.url, content=body.encode(), headers=headers)
        except httpx.HTTPError as e:
            log.warning("ntfy failed: %s", e)

    async def handle(self, listing: Listing) -> None:
        await self.send(listing.title, _fmt(listing), listing.url)
