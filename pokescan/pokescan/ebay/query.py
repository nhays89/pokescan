from __future__ import annotations
import re
from pokescan.config.models import GlobalConfig, SearchConfig


def _term(t: str) -> str:
    return f'"{t}"' if " " in t.strip() else t.strip()


def _year_mode(search: SearchConfig, g: GlobalConfig | None) -> str:
    return search.year_match or (g.year_match if g else "aspect")


def expand_queries(search: SearchConfig, g: GlobalConfig | None = None) -> list[str]:
    """One query string per batch of sets, e.g. 'pokemon ("call of legends", undaunted)'.
    No sets -> just the base query. In year_match=title mode the years are appended as a second
    OR-group. Each string costs one API call per poll (per page)."""
    year_group = ""
    if search.years and _year_mode(search, g) == "title":
        ys = ", ".join(str(y) for y in search.years)
        year_group = f" ({ys})" if len(search.years) > 1 else f" {ys}"
    if not search.sets:
        return [search.q + year_group]
    n = search.set_batch_size
    out = []
    for i in range(0, len(search.sets), n):
        chunk = [_term(x) for x in search.sets[i:i + n]]
        base = f"{search.q} {chunk[0]}" if len(chunk) == 1 else f"{search.q} ({', '.join(chunk)})"
        out.append(base + year_group)
    return out


def build_params(search: SearchConfig, g: GlobalConfig, offset: int = 0, q: str | None = None) -> dict[str, str]:
    """SearchConfig -> Browse API query params."""
    f = search.filters
    parts: list[str] = []
    if f.price_min is not None or f.price_max is not None:
        lo = "" if f.price_min is None else f"{f.price_min:g}"
        hi = "" if f.price_max is None else f"{f.price_max:g}"
        parts.append(f"price:[{lo}..{hi}]" if hi else f"price:[{lo}]")
        parts.append("priceCurrency:USD")
    if f.buying_options:
        parts.append("buyingOptions:{" + "|".join(f.buying_options) + "}")
    if f.conditions:
        parts.append("conditions:{" + "|".join(f.conditions) + "}")

    params = {"q": q or search.q, "sort": "newlyListed",
              "limit": str(g.page_size), "offset": str(offset)}
    cats = search.category_ids or g.category_ids
    if cats:
        params["category_ids"] = ",".join(cats)
    if parts:
        params["filter"] = ",".join(parts)

    aspects: list[str] = []
    langs = g.languages if search.languages is None else search.languages
    if langs:
        aspects.append(f"Language:{{{'|'.join(langs)}}}")
    if search.years and _year_mode(search, g) == "aspect":
        aspects.append(f"{g.year_aspect_name}:{{{'|'.join(str(y) for y in search.years)}}}")
    if aspects:
        if len(cats) != 1:
            raise ValueError(f"[{search.name}] language/year aspect filters need exactly one category id (eBay requirement)")
        params["aspect_filter"] = f"categoryId:{cats[0]}," + ",".join(aspects)
    return params


def full_res(url: str) -> str:
    """eBay image URLs embed a size (s-l225.jpg). Swap for the 1600px variant, no API call."""
    return re.sub(r"s-l\d+", "s-l1600", url)
