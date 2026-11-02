from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Listing:
    item_id: str
    title: str
    price: float | None
    currency: str
    url: str | None
    image_url: str | None
    seller: str | None
    buying_options: tuple[str, ...] = ()
    created_at: str | None = None
    search_name: str = ""


from typing import Literal  # noqa: E402

Severity = Literal["none", "minor", "moderate", "major"]
SEVERITY_ORDER = {"none": 0, "minor": 1, "moderate": 2, "major": 3}
