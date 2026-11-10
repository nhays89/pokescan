from __future__ import annotations
import re

# eBay item-specific names (lower-cased) -> our fields. Verify against real listings (raw aspects are kept).
ALIASES = {
    "card_name": ["card name"],
    "set_name": ["set", "set name"],
    "year": ["year manufactured", "year"],
    "card_number": ["card number", "number"],
    "rarity": ["rarity"],
    "finish": ["finish"],
    "language": ["language"],
}
NAME_FALLBACK = ["character", "pokémon", "pokemon"]
_NUM = re.compile(r"(?<![\d/])(\d{1,3})\s*/\s*(\d{1,3})(?![\d/])")
_YEAR = re.compile(r"\b(199[6-9]|20[0-2]\d)\b")


def extract_card_details(title: str, aspects: list[dict] | None) -> dict:
    """Best-effort card metadata. eBay item specifics win; title parsing fills gaps (year, #/total)."""
    by_name: dict[str, str] = {}
    for a in aspects or []:
        n, v = str(a.get("name", "")).strip().lower(), a.get("value")
        if n and v not in (None, ""):
            by_name.setdefault(n, str(v).strip())
    out: dict = {k: None for k in ALIASES}
    from_aspects = False
    for field, names in ALIASES.items():
        for n in names:
            if n in by_name:
                out[field], from_aspects = by_name[n], True
                break
    if not out["card_name"]:
        for n in NAME_FALLBACK:
            if n in by_name:
                out["card_name"], from_aspects = by_name[n], True
                break
    if out["year"] is not None:
        m = _YEAR.search(str(out["year"]))
        out["year"] = int(m.group(1)) if m else None
    if out["year"] is None and (m := _YEAR.search(title or "")):
        out["year"] = int(m.group(1))
    if not out["card_number"] and (m := _NUM.search(title or "")):
        out["card_number"] = f"{m.group(1)}/{m.group(2)}"
    out["source"] = "ebay_aspects" if from_aspects else "title"
    return out
