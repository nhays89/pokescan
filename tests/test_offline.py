import json
from pokescan.config.models import AppConfig
from pokescan.ebay.models import ItemSummary
from pokescan.ebay.query import build_params, full_res
from pokescan.storage import db
from pokescan.storage.repositories import ListingRepository, UsageRepository
from pokescan.pipeline.budget import RateBudget
from pokescan.ebay.errors import BudgetExhausted
import pytest, yaml, pathlib

cfg = AppConfig.model_validate(yaml.safe_load(pathlib.Path("config/searches.yaml").read_text()))

def test_query():
    p = build_params(cfg.searches[0], cfg.global_)
    assert p["sort"] == "newlyListed"
    assert "price:[" in p["filter"] and "buyingOptions:{" in p["filter"]
    assert p["category_ids"] == "183454"

def test_full_res():
    assert full_res("https://i.ebayimg.com/images/g/abc/s-l225.jpg").endswith("s-l1600.jpg")

def test_upsert_dedup():
    conn = db.connect(":memory:")
    repo = ListingRepository(conn)
    raw = {"itemId": "v1|1|0", "title": "Charizard Base Set", "price": {"value": "99.5", "currency": "USD"},
           "image": {"imageUrl": "https://i.ebayimg.com/x/s-l225.jpg"}, "itemWebUrl": "http://x"}
    item = ItemSummary.model_validate(raw); item.raw = raw
    assert repo.upsert("s", item)[1] is True
    assert repo.upsert("s", item)[1] is False

def test_budget():
    conn = db.connect(":memory:")
    b = RateBudget(UsageRepository(conn), 2)
    b.consume(); b.consume()
    with pytest.raises(BudgetExhausted):
        b.consume()


def test_expand_sets():
    from pokescan.ebay.query import expand_queries
    from pokescan.config.models import SearchConfig
    sc = SearchConfig(name="x", sets=["undaunted", "call of legends", "unleashed", "triumphant"], set_batch_size=3)
    assert expand_queries(sc) == ['pokemon (undaunted, "call of legends", unleashed)', "pokemon triumphant"]


def test_language_filter():
    from pokescan.config.models import SearchConfig, GlobalConfig
    g = GlobalConfig(category_ids=["183454"], languages=["English"])
    p = build_params(SearchConfig(name="x", sets=["jungle"]), g)
    assert p["aspect_filter"] == "categoryId:183454,Language:{English}"
    assert "aspect_filter" not in build_params(SearchConfig(name="x", languages=[]), g)       # per-search override off
    assert build_params(SearchConfig(name="x", languages=["English", "French"]), g)["aspect_filter"].endswith("{English|French}")
    with pytest.raises(ValueError):
        build_params(SearchConfig(name="x"), GlobalConfig(languages=["English"]))                # no category -> refuse


def test_year_aspect_and_language_combined():
    from pokescan.config.models import SearchConfig, GlobalConfig
    g = GlobalConfig(category_ids=["183454"], languages=["English"])
    p = build_params(SearchConfig(name="x", years=[1999, 2000]), g)
    assert p["aspect_filter"] == "categoryId:183454,Language:{English},Year Manufactured:{1999|2000}"
    p = build_params(SearchConfig(name="x", years=[1999], languages=[]), g)
    assert p["aspect_filter"] == "categoryId:183454,Year Manufactured:{1999}"
    assert p["q"] == "pokemon"                       # aspect mode leaves the keyword query alone

def test_year_title_mode():
    from pokescan.ebay.query import expand_queries
    from pokescan.config.models import SearchConfig, GlobalConfig
    g = GlobalConfig(year_match="title")
    assert expand_queries(SearchConfig(name="x", years=[1999, 2000]), g) == ["pokemon (1999, 2000)"]
    assert expand_queries(SearchConfig(name="x", years=[1999]), g) == ["pokemon 1999"]
    sc = SearchConfig(name="x", years=[1999, 2000], sets=["jungle", "fossil"], set_batch_size=2)
    assert expand_queries(sc, g) == ["pokemon (jungle, fossil) (1999, 2000)"]
    assert "aspect_filter" not in build_params(sc, GlobalConfig(year_match="title"))   # title mode adds no aspect filter


def test_generic_aspects_merge():
    from pokescan.config.models import SearchConfig, GlobalConfig
    g = GlobalConfig(category_ids=["183454"], aspects={"Game": ["Pokémon TCG"]})
    p = build_params(SearchConfig(name="x"), g)
    assert p["aspect_filter"] == "categoryId:183454,Game:{Pokémon TCG}"
    p = build_params(SearchConfig(name="x", aspects={"Game": ["Pokémon TCG"], "Graded": ["No"]}), g)
    assert p["aspect_filter"].endswith("Game:{Pokémon TCG},Graded:{No}")
    assert "Game" not in build_params(SearchConfig(name="x", aspects={}), GlobalConfig(category_ids=["1"])).get("aspect_filter", "")
