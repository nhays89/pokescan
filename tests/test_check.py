import yaml, pathlib
from pokescan.check import planned_requests
from pokescan.config.models import AppConfig

def test_planned_requests_match_config():
    cfg = AppConfig.model_validate(yaml.safe_load(pathlib.Path("config/searches.yaml").read_text()))
    plan = planned_requests(cfg)
    assert len(plan) == sum(1 for s in cfg.all_searches() if s.enabled)          # year-only searches: 1 query each
    for name, q, params, url in plan:
        assert params["sort"] == "newlyListed" and url.startswith("https://api.ebay.com/buy/browse/v1/item_summary/search?")
        assert " " not in url
