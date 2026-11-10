from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from pokescan.domain.models import Listing
from pokescan.ebay.models import ItemSummary


class ListingRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def search_has_history(self, search_name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM listing_matches WHERE search_name=? LIMIT 1", (search_name,)).fetchone()
        return row is not None

    def upsert(self, search_name: str, item: ItemSummary) -> tuple[Listing, bool]:
        """Returns (listing, is_new). New == item_id not previously in the table."""
        existed = self.conn.execute(
            "SELECT 1 FROM listings WHERE item_id=?", (item.itemId,)).fetchone() is not None
        price = item.price.value if item.price else None
        currency = item.price.currency if item.price else None
        seller = item.seller.username if item.seller else None
        with self.conn:
            self.conn.execute(
                """INSERT INTO listings (item_id,title,price,currency,url,seller,buying_options,created_at,raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(item_id) DO UPDATE SET
                     last_seen_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                     price=excluded.price, title=excluded.title, raw_json=excluded.raw_json""",
                (item.itemId, item.title, price, currency, item.itemWebUrl, seller,
                 ",".join(item.buyingOptions), item.itemCreationDate, json.dumps(item.raw)))
            self.conn.execute(
                "INSERT OR IGNORE INTO listing_matches (item_id, search_name) VALUES (?,?)",
                (item.itemId, search_name))
            images = ([item.image] if item.image else []) + list(item.additionalImages)
            for pos, img in enumerate(images):
                self.conn.execute(
                    "INSERT OR IGNORE INTO listing_images (item_id, position, url) VALUES (?,?,?)",
                    (item.itemId, pos, img.imageUrl))
        listing = Listing(
            item_id=item.itemId, title=item.title, price=price, currency=currency or "USD",
            url=item.itemWebUrl, image_url=item.image.imageUrl if item.image else None,
            seller=seller, buying_options=tuple(item.buyingOptions),
            created_at=item.itemCreationDate, search_name=search_name)
        return listing, not existed


    def record_images(self, item_id: str, urls: list[str], paths: list) -> None:
        with self.conn:
            for pos, (u, path) in enumerate(zip(urls, paths)):
                self.conn.execute(
                    "INSERT INTO listing_images (item_id, position, url, local_path) VALUES (?,?,?,?) "
                    "ON CONFLICT(item_id, position) DO UPDATE SET url=excluded.url, local_path=excluded.local_path",
                    (item_id, pos, u, str(path) if path else None))


class AssessmentRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def count_today(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM assessments WHERE substr(created_at,1,10)=strftime('%Y-%m-%d','now')"
        ).fetchone()[0]

    def has_assessment(self, item_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM assessments WHERE item_id=?", (item_id,)).fetchone() is not None

    def add_rule_fail(self, item_id: str, reason: str) -> None:
        """Record an automatic FAIL decided from listing metadata (no photos fetched, no model call)."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO assessments (item_id, model, grade, verdict, defects_json) VALUES (?,?,?,?,?)",
                (item_id, "rule", "n/a", "fail", json.dumps({"verdict": "fail", "reasons": [reason]})))

    def add(self, item_id: str, report, result) -> None:
        payload = {"report": report.model_dump(), "verdict": result.verdict.value, "reasons": result.reasons}
        with self.conn:
            self.conn.execute(
                "INSERT INTO assessments (item_id, model, grade, verdict, defects_json, confidence) VALUES (?,?,?,?,?,?)",
                (item_id, report.model, report.overall, result.verdict.value, json.dumps(payload),
                 min((d.confidence for d in report.defects), default=None)))


class NotificationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def exists(self, item_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM notifications WHERE item_id=?", (item_id,)).fetchone() is not None

    def mark(self, item_id: str, channel: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO notifications (item_id, channel) VALUES (?,?)", (item_id, channel))


class CardDetailsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(self, item_id: str, meta: dict, aspects: list | None = None) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO card_details (item_id, card_name, set_name, year, card_number, rarity, finish,
                                             language, source, aspects_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(item_id) DO UPDATE SET
                     card_name=excluded.card_name, set_name=excluded.set_name, year=excluded.year,
                     card_number=excluded.card_number, rarity=excluded.rarity, finish=excluded.finish,
                     language=excluded.language, source=excluded.source, aspects_json=excluded.aspects_json,
                     updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
                (item_id, meta.get("card_name"), meta.get("set_name"), meta.get("year"), meta.get("card_number"),
                 meta.get("rarity"), meta.get("finish"), meta.get("language"), meta.get("source"),
                 json.dumps(aspects) if aspects else None))

    def fill_name(self, item_id: str, name: str, source: str = "vision") -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE card_details SET card_name=?, source=source || '+' || ? "
                "WHERE item_id=? AND (card_name IS NULL OR card_name='')", (name, source, item_id))


class PollRunRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record(self, search_name: str, calls: int, fetched: int, new: int, error: str | None = None):
        with self.conn:
            self.conn.execute(
                "INSERT INTO poll_runs (search_name,calls,fetched,new_listings,error) VALUES (?,?,?,?,?)",
                (search_name, calls, fetched, new, error))


class UsageRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def calls_today(self) -> int:
        row = self.conn.execute("SELECT calls FROM api_usage WHERE day=?", (self._today(),)).fetchone()
        return row["calls"] if row else 0

    def increment(self) -> int:
        with self.conn:
            self.conn.execute(
                "INSERT INTO api_usage (day, calls) VALUES (?,1) "
                "ON CONFLICT(day) DO UPDATE SET calls=calls+1", (self._today(),))
        return self.calls_today()
