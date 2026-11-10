from pokescan.domain.card_meta import extract_card_details
from pokescan.search import parse_args, make_search
from pokescan.ebay.query import build_params
from pokescan.config.models import GlobalConfig
from pokescan.storage import db
from pokescan.storage.repositories import CardDetailsRepository


def test_aspects_win_title_fills_gaps():
    aspects = [{"name": "Set", "value": "Base Set"}, {"name": "Year Manufactured", "value": "1999"},
               {"name": "Rarity", "value": "Rare Holo"}, {"name": "Language", "value": "English"}]
    m = extract_card_details("Pokemon Charizard 4/102 Holo", aspects)
    assert (m["set_name"], m["year"], m["card_number"], m["rarity"], m["source"]) == ("Base Set", 1999, "4/102", "Rare Holo", "ebay_aspects")

def test_title_only_fallback():
    m = extract_card_details("1999 Pokemon Jungle Pinsir 9/64 NM", [])
    assert m["year"] == 1999 and m["card_number"] == "9/64" and m["set_name"] is None and m["source"] == "title"

def test_no_false_card_number_from_dates():
    assert extract_card_details("PSA 10 2021/05/12 lot", [])["card_number"] is None

def test_manual_search_params():
    args = parse_args(["dark golduck", "--min-price", "5", "--max-price", "300", "--exclude", "lot"])
    p = build_params(make_search(args), GlobalConfig(category_ids=["183454"]))
    assert p["q"] == "dark golduck" and p["filter"].startswith("price:[5..300]") and "aspect_filter" not in p

def test_matches_view_joins_card_details():
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO listings (item_id,title,raw_json,price) VALUES ('a','Dark Golduck 1st Ed','{}',99)")
    conn.execute("INSERT INTO assessments (item_id,model,grade,verdict) VALUES ('a','classical-v1','light_play','pass')")
    CardDetailsRepository(conn).upsert("a", {"card_name": "Dark Golduck", "set_name": "Team Rocket", "year": 2000,
                                              "card_number": "34/82", "source": "ebay_aspects"}, [{"name": "Set", "value": "Team Rocket"}])
    row = conn.execute("SELECT card_name, set_name, year, verdict FROM matches").fetchone()
    assert tuple(row) == ("Dark Golduck", "Team Rocket", 2000, "pass")
    assert conn.execute("SELECT aspects_json FROM card_details").fetchone()[0].startswith("[")
