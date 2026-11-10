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
        if listing.search_name.startswith("watch:"):
            return   # watchlist hits are announced by WatchNotifier
        await self.send(listing.title, _fmt(listing), listing.url)


class WatchNotifier:
    """Pushes every new watchlist listing immediately. If the card is set to analyze (and analysis is on),
    the AnalysisHandler sends the push instead, with the verdict included."""

    def __init__(self, notifier, loader):
        self.notifier, self.loader = notifier, loader

    async def handle(self, listing: Listing) -> None:
        if not listing.search_name.startswith("watch:"):
            return
        cfg = self.loader.current()
        name = listing.search_name[len("watch:"):]
        card = next((w for w in cfg.watch_cards() if w.name == name), None)
        if card and card.analyze and cfg.analysis.enabled:
            return
        price = f"{listing.price:.2f} {listing.currency}" if listing.price is not None else "n/a"
        kind = "/".join(listing.buying_options[:2]).lower().replace("_", " ")
        body = f"{price}{' | ' + kind if kind else ''}\n{listing.title}\n{listing.url or ''}"
        await self.notifier.send(f"WATCH: {name}", body, listing.url)
