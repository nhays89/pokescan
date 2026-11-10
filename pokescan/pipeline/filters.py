from pokescan.config.models import SearchConfig
from pokescan.ebay.models import ItemSummary


def passes(item: ItemSummary, search: SearchConfig) -> bool:
    title = item.title.lower()
    return not any(term.lower() in title for term in search.exclude_terms)
