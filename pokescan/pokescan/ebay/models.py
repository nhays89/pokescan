from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class Price(BaseModel):
    value: float
    currency: str = "USD"


class Image(BaseModel):
    imageUrl: str


class Seller(BaseModel):
    username: str | None = None
    feedbackPercentage: str | None = None
    feedbackScore: int | None = None


class ItemSummary(BaseModel):
    """One element of itemSummaries in Browse search. Unknown fields are ignored."""
    itemId: str
    title: str
    price: Price | None = None
    image: Image | None = None
    additionalImages: list[Image] = []
    itemWebUrl: str | None = None
    seller: Seller | None = None
    buyingOptions: list[str] = []
    condition: str | None = None
    itemCreationDate: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


class SearchPage(BaseModel):
    items: list[ItemSummary]
    total: int = 0
    next_url: str | None = None
    refinement: dict[str, Any] = {}
