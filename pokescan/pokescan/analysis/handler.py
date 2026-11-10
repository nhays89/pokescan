from __future__ import annotations
import asyncio, logging, re
from pokescan.analysis.policy import QualityPolicy, Verdict
from pokescan.domain.models import Listing
from pokescan.ebay.errors import EbayError

log = logging.getLogger(__name__)


class AnalysisHandler:
    """ListingHandler for Phase 2. handle() only enqueues, so slow image downloads and
    model calls never block the eBay polling loop; run() drains the queue."""

    def __init__(self, loader, client, images, analyzer, notifier, listings, assessments, notifications,
                 queue_size: int = 5000):
        self.loader, self.client, self.images, self.analyzer = loader, client, images, analyzer
        self.notifier, self.listings = notifier, listings
        self.assessments, self.notifications = assessments, notifications
        self.queue: asyncio.Queue[Listing] = asyncio.Queue(maxsize=queue_size)

    async def handle(self, listing: Listing) -> None:
        try:
            self.queue.put_nowait(listing)
        except asyncio.QueueFull:
            log.warning("analysis queue full, dropping %s", listing.item_id)

    async def run(self) -> None:
        while True:
            listing = await self.queue.get()
            try:
                await self.process(listing)
            except Exception:
                log.exception("analysis failed for %s", listing.item_id)
            finally:
                self.queue.task_done()

    @staticmethod
    def _skip_by_title(title: str, terms: list[str]) -> str | None:
        for t in terms:
            if t.isascii():
                if re.search(rf"\b{re.escape(t)}\b", title, re.IGNORECASE):
                    return t
            elif t in title:  # CJK has no spaces/word boundaries: plain substring
                return t
        return None

    async def process(self, listing: Listing) -> None:
        cfg = self.loader.current().analysis
        if not cfg.enabled or self.assessments.has_assessment(listing.item_id):
            return
        if (term := self._skip_by_title(listing.title, cfg.auto_fail_title_terms)):
            self.assessments.add_rule_fail(listing.item_id, f"auto-fail: title contains {term!r}")
            log.info("auto-fail %s (title term %r)", listing.item_id, term)
            return
        if (term := self._skip_by_title(listing.title, cfg.skip_title_terms)):
            log.info("skip %s (title term %r)", listing.item_id, term)
            return
        if self.assessments.count_today() >= cfg.max_analyses_per_day:
            log.warning("daily analysis cap (%d) reached", cfg.max_analyses_per_day)
            return

        urls = [listing.image_url] if listing.image_url else []
        if cfg.fetch_all_photos:
            try:
                detail = await self.client.get_item(listing.item_id)
                got = ([detail["image"]["imageUrl"]] if detail.get("image") else []) + \
                      [i["imageUrl"] for i in detail.get("additionalImages", [])]
                urls = got or urls
                lang = next((a.get("value") for a in detail.get("localizedAspects", [])
                             if str(a.get("name", "")).lower() == "language"), None)
                if lang and cfg.allowed_languages and not any(
                        a.lower() in str(lang).lower() for a in cfg.allowed_languages):
                    self.assessments.add_rule_fail(listing.item_id, f"auto-fail: Language is {lang!r}")
                    log.info("auto-fail %s (Language=%r)", listing.item_id, lang)
                    return
            except EbayError as e:  # includes BudgetExhausted: fall back to the thumbnail
                log.warning("getItem failed for %s (%s); using thumbnail only", listing.item_id, e)
        urls = urls[:cfg.max_images]
        if not urls:
            return

        paths = await self.images.download(listing.item_id, urls)
        self.listings.record_images(listing.item_id, urls, paths)
        good = [p for p in paths if p]
        if not good:
            log.warning("no images downloaded for %s", listing.item_id)
            return

        report = await self.analyzer.analyze(listing, good)
        result = QualityPolicy(cfg.policy).evaluate(report)
        self.assessments.add(listing.item_id, report, result)
        log.info("%s -> %s %s", listing.item_id, result.verdict.value, result.reasons)

        if result.verdict == Verdict.FAIL or self.notifications.exists(listing.item_id):
            return
        if result.verdict == Verdict.REVIEW and not cfg.policy.notify_review:
            return
        back_only = report.scope == "back_only"
        tag = ("BACK OK" if back_only else "PASS") if result.verdict == Verdict.PASS else "REVIEW"
        price = f"{listing.price:.2f} {listing.currency}" if listing.price is not None else "n/a"
        cond = "back-only check, now look at the front" if back_only else report.overall.replace("_", " ")
        lines = [f"{price} | {cond} | {len(good)} photos", report.summary]
        if result.reasons:
            lines.append("Check: " + "; ".join(result.reasons))
        elif report.defects:
            lines.append("Minor: " + "; ".join(f"{d.severity} {d.type.replace('_', ' ')}" for d in report.defects))
        if report.unassessed_zones:
            lines.append("Not checked (glare/obscured): " + ", ".join(report.unassessed_zones))
        lines.append(listing.url or "")
        await self.notifier.send(f"{tag}: {listing.title[:80]}", "\n".join(x for x in lines if x), listing.url)
        self.notifications.mark(listing.item_id, "push")
