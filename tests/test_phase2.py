import asyncio, yaml, pathlib
from pokescan.analysis.base import ConditionReport
from pokescan.analysis.claude_vision import _extract_json
from pokescan.analysis.handler import AnalysisHandler
from pokescan.analysis.policy import QualityPolicy, Verdict
from pokescan.config.models import AppConfig, PolicyConfig
from pokescan.domain.models import Listing
from pokescan.ebay.models import ItemSummary
from pokescan.storage import db
from pokescan.storage.repositories import AssessmentRepository, ListingRepository, NotificationRepository


def report(**kw):
    base = dict(image_quality="good", front_visible=True, back_visible=True, overall="near_mint_or_better", defects=[])
    base.update(kw)
    return ConditionReport.model_validate(base)


def d(t, sev, conf=0.9):
    return {"type": t, "location": "back corners", "severity": sev, "confidence": conf}


P = QualityPolicy(PolicyConfig())


def test_pass():
    assert P.evaluate(report(defects=[d("whitening", "minor")])).verdict == Verdict.PASS

def test_fail_major_whitening():
    assert P.evaluate(report(defects=[d("whitening", "major")])).verdict == Verdict.FAIL

def test_crease_any_fails():
    assert P.evaluate(report(defects=[d("crease", "minor")])).verdict == Verdict.FAIL

def test_low_confidence_defect_is_review():
    assert P.evaluate(report(defects=[d("scratch", "major", 0.3)])).verdict == Verdict.REVIEW

def test_unclear_photos_review():
    assert P.evaluate(report(image_quality="insufficient")).verdict == Verdict.REVIEW

def test_no_back_review():
    assert P.evaluate(report(back_visible=False)).verdict == Verdict.REVIEW

def test_slab_fail():
    assert P.evaluate(report(is_graded_slab=True)).verdict == Verdict.FAIL

def test_unknown_labels_degrade():
    r = report(defects=[{"type": "Weird Thing", "severity": "huge", "confidence": 0.8}])
    assert r.defects[0].type == "other" and r.defects[0].severity == "moderate"

def test_extract_json_with_fences():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


# ---- handler with fakes ----
class Loader:
    def __init__(self, enabled=True):
        cfg = yaml.safe_load(pathlib.Path("config/searches.yaml").read_text())
        cfg["analysis"]["enabled"] = enabled
        self.cfg = AppConfig.model_validate(cfg)
    def current(self): return self.cfg

class FakeClient:
    async def get_item(self, item_id):
        return {"image": {"imageUrl": "http://x/s-l500.jpg"}, "additionalImages": [{"imageUrl": "http://x/b.jpg"}]}

class FakeImages:
    def __init__(self, tmp): self.tmp = tmp
    async def download(self, item_id, urls):
        out = []
        for i, _ in enumerate(urls):
            p = self.tmp / f"{i}.jpg"; p.write_bytes(b"x"); out.append(p)
        return out

class FakeAnalyzer:
    def __init__(self, rep): self.rep, self.calls = rep, 0
    async def analyze(self, listing, images):
        self.calls += 1
        self.rep.model = "fake"
        return self.rep

class Sink:
    def __init__(self): self.sent = []
    async def send(self, title, body, url=None): self.sent.append((title, body))


def make(tmp_path, rep, enabled=True):
    conn = db.connect(":memory:")
    listings = ListingRepository(conn)
    raw = {"itemId": "v1|9|0", "title": "Charizard Base Set Holo", "price": {"value": "120", "currency": "USD"},
           "image": {"imageUrl": "http://x/s-l225.jpg"}, "itemWebUrl": "http://item"}
    item = ItemSummary.model_validate(raw); item.raw = raw
    listing, _ = listings.upsert("s", item)
    sink, analyzer = Sink(), FakeAnalyzer(rep)
    h = AnalysisHandler(Loader(enabled), FakeClient(), FakeImages(tmp_path), analyzer, sink,
                        listings, AssessmentRepository(conn), NotificationRepository(conn))
    return h, listing, sink, analyzer, conn


def test_handler_pass_notifies_once(tmp_path):
    h, listing, sink, an, conn = make(tmp_path, report())
    asyncio.run(h.process(listing)); asyncio.run(h.process(listing))
    assert len(sink.sent) == 1 and sink.sent[0][0].startswith("PASS")
    assert an.calls == 1                       # second call short-circuits on existing assessment
    assert conn.execute("SELECT verdict FROM assessments").fetchone()[0] == "pass"
    assert conn.execute("SELECT COUNT(*) FROM listing_images WHERE local_path IS NOT NULL").fetchone()[0] == 2

def test_handler_fail_silent_but_stored(tmp_path):
    h, listing, sink, an, conn = make(tmp_path, report(defects=[d("crease", "moderate")]))
    asyncio.run(h.process(listing))
    assert sink.sent == [] and conn.execute("SELECT verdict FROM assessments").fetchone()[0] == "fail"

def test_handler_disabled(tmp_path):
    h, listing, sink, an, _ = make(tmp_path, report(), enabled=False)
    asyncio.run(h.process(listing))
    assert an.calls == 0

def test_skip_title_term(tmp_path):
    h, listing, sink, an, _ = make(tmp_path, report())
    l2 = Listing(**{**listing.__dict__, "title": "PSA 9 Charizard"})
    asyncio.run(h.process(l2))
    assert an.calls == 0


def test_japanese_auto_fail_costs_nothing(tmp_path):
    h, listing, sink, an, conn = make(tmp_path, report())
    calls = []
    async def spy(item_id): calls.append(item_id); return {}
    h.client.get_item = spy
    for title in ("Charizard Japanese Base Set Holo", "Blastoise JP Holo 1996", "Venusaur JPN rare"):
        l = Listing(**{**listing.__dict__, "title": title, "item_id": title})
        conn.execute("INSERT INTO listings (item_id,title,raw_json) VALUES (?,?,?)", (title, title, "{}"))
        asyncio.run(h.process(l))
    assert an.calls == 0 and calls == [] and sink.sent == []
    rows = conn.execute("SELECT model, verdict FROM assessments").fetchall()
    assert len(rows) == 3 and all(r["verdict"] == "fail" and r["model"] == "rule" for r in rows)

def test_english_listing_not_caught_by_japanese_rule(tmp_path):
    h, listing, sink, an, conn = make(tmp_path, report())
    asyncio.run(h.process(listing))      # "Charizard Base Set Holo"
    assert an.calls == 1


def test_cjk_term_matches_inside_text():
    assert AnalysisHandler._skip_by_title("リザードン 日本語版 初版", ["日本語"]) == "日本語"
    assert AnalysisHandler._skip_by_title("Charizard Japanese", ["japanese"]) == "japanese"
    assert AnalysisHandler._skip_by_title("Jumpluff Holo", ["jp"]) is None   # whole-word only


def _with_language(h, value):
    async def gi(item_id):
        aspects = [{"type": "STRING", "name": "Language", "value": value}] if value else [{"name": "Set", "value": "Jungle"}]
        return {"image": {"imageUrl": "http://x/a.jpg"}, "additionalImages": [], "localizedAspects": aspects}
    h.client.get_item = gi

def test_language_aspect_japanese_fails_before_photos(tmp_path):
    cfgd = None
    h, listing, sink, an, conn = make(tmp_path, report())
    h.loader.cfg.analysis.allowed_languages = ["English"]
    _with_language(h, "Japanese")
    asyncio.run(h.process(listing))
    assert an.calls == 0 and sink.sent == []
    assert conn.execute("SELECT verdict, model FROM assessments").fetchone()[:] == ("fail", "rule")
    assert conn.execute("SELECT COUNT(*) FROM listing_images WHERE local_path IS NOT NULL").fetchone()[0] == 0

def test_language_english_and_blank_pass_through(tmp_path):
    for value in ("English", None):
        h, listing, sink, an, conn = make(tmp_path, report())
        h.loader.cfg.analysis.allowed_languages = ["English"]
        _with_language(h, value)
        asyncio.run(h.process(listing))
        assert an.calls == 1, value


def test_handler_stores_card_details_and_adds_them_to_the_push(tmp_path):
    from pokescan.storage.repositories import CardDetailsRepository
    h, listing, sink, an, conn = make(tmp_path, report())
    h.cards = CardDetailsRepository(conn)
    async def gi(item_id):
        return {"image": {"imageUrl": "http://x/a.jpg"}, "additionalImages": [{"imageUrl": "http://x/b.jpg"}],
                "localizedAspects": [{"name": "Set", "value": "Base Set"}, {"name": "Year Manufactured", "value": "1999"}]}
    h.client.get_item = gi
    asyncio.run(h.process(listing))
    row = conn.execute("SELECT set_name, year, source FROM card_details").fetchone()
    assert tuple(row) == ("Base Set", 1999, "ebay_aspects")
    assert "Base Set 1999" in sink.sent[0][1]
