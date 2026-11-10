import asyncio, yaml, pathlib, pytest
from pokescan.config.models import AppConfig
from pokescan.domain.models import Listing
from pokescan.ebay.query import build_params
from pokescan.notify.handlers import NtfyNotifier, WatchNotifier
from pokescan.check import planned_requests
from test_poller import dsetup, GL, iso
from test_phase2 import make, report, Sink
from pokescan.config.models import SearchConfig

BASE = yaml.safe_load(pathlib.Path("config/searches.yaml").read_text())

def cfg_with(watchlist, analysis_enabled=False):
    d = {**BASE, "watchlist": watchlist}
    d["analysis"] = {**d["analysis"], "enabled": analysis_enabled}
    return AppConfig.model_validate(d)

class L:
    def __init__(self, cfg): self.cfg = cfg
    def current(self): return self.cfg

def listing(search_name, title="Dark Golduck 34/82 Holo"):
    return Listing(item_id="v1|1|0", title=title, price=45.0, currency="USD", url="http://i", image_url=None,
                   seller=None, buying_options=("FIXED_PRICE",), search_name=search_name)

def test_defaults_merge_and_search_conversion():
    cfg = cfg_with({"defaults": {"priority": "high", "exclude_terms": ["lot"], "lookback_days": 0},
                    "cards": [{"name": "dark golduck", "query": '"dark golduck"', "max_price": 150},
                              {"name": "off", "enabled": False},
                              {"name": "char", "priority": "low", "exclude_terms": []}]})
    w = {x.name: x for x in cfg.watch_cards()}
    assert w["dark golduck"].priority == "high" and w["dark golduck"].exclude_terms == ["lot"]
    assert w["char"].priority == "low" and w["char"].exclude_terms == [] and w["char"].query == "char"
    names = [s.name for s in cfg.all_searches()]
    assert "watch:dark golduck" in names and "watch:char" in names and "watch:off" not in names
    s = next(s for s in cfg.all_searches() if s.name == "watch:dark golduck")
    p = build_params(s, cfg.global_)
    assert p["q"] == '"dark golduck"' and p["filter"] == "price:[..150],priceCurrency:USD" and "Year" not in p.get("aspect_filter", "")

def test_duplicate_card_names_rejected():
    with pytest.raises(Exception):
        cfg_with({"cards": [{"name": "a"}, {"name": "a"}]})

def test_notify_existing_reports_first_run():
    quiet = SearchConfig(name="watch:x", lookback_days=0)
    loud = SearchConfig(name="watch:x", lookback_days=0, notify_existing=True)
    p1, _ = dsetup([[(1, 400)]]); assert asyncio.run(p1.poll(quiet, GL)) == []          # seeded silently
    p2, _ = dsetup([[(1, 400)]]); assert len(asyncio.run(p2.poll(loud, GL))) == 1       # existing reported

def test_watch_notifier_pushes_watch_hits_only():
    sink = Sink(); n = WatchNotifier(sink, L(cfg_with({"cards": [{"name": "dark golduck"}]})))
    asyncio.run(n.handle(listing("watch:dark golduck"))); asyncio.run(n.handle(listing("wotc-era")))
    assert [t for t, _ in sink.sent] == ["WATCH: dark golduck"]

def test_watch_notifier_defers_to_analysis_when_card_analyzes():
    cards = {"cards": [{"name": "dark golduck", "analyze": True}]}
    for enabled, expected in ((True, []), (False, ["WATCH: dark golduck"])):      # analysis off -> still never silent
        sink = Sink(); n = WatchNotifier(sink, L(cfg_with(cards, analysis_enabled=enabled)))
        asyncio.run(n.handle(listing("watch:dark golduck"))); assert [t for t, _ in sink.sent] == expected

def test_ntfy_phase1_push_ignores_watch_hits():
    posted = []
    class Http:
        async def post(self, url, content=None, headers=None): posted.append(1)
    n = NtfyNotifier("t", Http()); asyncio.run(n.handle(listing("watch:x"))); asyncio.run(n.handle(listing("wotc-era")))
    assert len(posted) == 1

def test_plain_watch_hit_is_not_analyzed(tmp_path):
    h, lst, sink, an, conn = make(tmp_path, report())
    h.loader.cfg = cfg_with({"cards": [{"name": "dark golduck"}]}, analysis_enabled=True)
    asyncio.run(h.handle(Listing(**{**lst.__dict__, "search_name": "watch:dark golduck"})))
    assert h.queue.qsize() == 0

def test_analyzed_watch_hit_notifies_even_on_fail_and_ignores_skip_terms(tmp_path):
    from pokescan.analysis.base import ConditionReport
    bad = ConditionReport.model_validate({"image_quality": "good", "front_visible": True, "back_visible": True,
        "overall": "heavy_play", "defects": [{"type": "crease", "severity": "major", "confidence": 0.9, "location": "x"}]})
    h, lst, sink, an, conn = make(tmp_path, bad)
    h.loader.cfg = cfg_with({"cards": [{"name": "dark golduck", "analyze": True}]}, analysis_enabled=True)
    h.loader.cfg.analysis.skip_title_terms = ["psa"]
    w = Listing(**{**lst.__dict__, "search_name": "watch:dark golduck", "title": "PSA 8 Dark Golduck"})
    asyncio.run(h.handle(w)); assert h.queue.qsize() == 1
    asyncio.run(h.process(w))
    assert sink.sent and sink.sent[0][0].startswith("WATCH FAIL")

def test_dry_run_plan_includes_watch_requests():
    cfg = cfg_with({"cards": [{"name": "dark golduck", "query": '"dark golduck"', "max_price": 150}]})
    plan = planned_requests(cfg)
    assert any(name == "watch:dark golduck" for name, *_ in plan)
