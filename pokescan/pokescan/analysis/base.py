from __future__ import annotations
from pathlib import Path
from typing import Literal, Protocol
from pydantic import BaseModel, Field, field_validator
from pokescan.domain.models import Listing, Severity, SEVERITY_ORDER

DEFECT_TYPES = {"whitening", "edge_wear", "corner_wear", "scratch", "crease", "dent", "stain", "print_line", "other"}


class Defect(BaseModel):
    type: str
    location: str = ""
    severity: Severity
    confidence: float = Field(ge=0, le=1)

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v):  # unknown labels from the model degrade to "other" instead of crashing
        v = str(v).lower().strip().replace(" ", "_")
        return v if v in DEFECT_TYPES else "other"

    @field_validator("severity", mode="before")
    @classmethod
    def _sev(cls, v):
        v = str(v).lower().strip()
        return v if v in SEVERITY_ORDER else "moderate"


class ConditionReport(BaseModel):
    card_name: str = ""
    is_graded_slab: bool = False
    looks_authentic: bool = True
    image_quality: Literal["good", "limited", "insufficient"]
    front_visible: bool
    back_visible: bool
    defects: list[Defect] = []
    centering: Literal["good", "off", "unknown"] = "unknown"
    overall: Literal["near_mint_or_better", "light_play", "moderate_play", "heavy_play", "unclear"]
    summary: str = ""
    scope: Literal["full", "back_only"] = "full"   # classical analyzer only judges the back
    unassessed_zones: list[str] = []               # edge/corner zones hidden by glare etc.
    # filled in by the analyzer, not requested from the model
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class ConditionAnalyzer(Protocol):
    async def analyze(self, listing: Listing, images: list[Path]) -> ConditionReport: ...
