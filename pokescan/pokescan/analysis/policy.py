from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pokescan.config.models import PolicyConfig
from pokescan.domain.models import SEVERITY_ORDER
from .base import ConditionReport


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    REVIEW = "review"   # photos unclear / model unsure -> a human should look


@dataclass
class PolicyResult:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)


class QualityPolicy:
    """Deterministic rules over the model's structured report. Tune in YAML, no re-analysis needed."""

    def __init__(self, cfg: PolicyConfig):
        self.cfg = cfg

    def evaluate(self, r: ConditionReport) -> PolicyResult:
        fails: list[str] = []
        reviews: list[str] = []

        if r.is_graded_slab and self.cfg.skip_graded:
            return PolicyResult(Verdict.FAIL, ["graded slab"])

        for d in r.defects:
            if SEVERITY_ORDER[d.severity] > SEVERITY_ORDER[self.cfg.limit(d.type)]:
                msg = f"{d.severity} {d.type.replace('_', ' ')} ({d.location or 'unspecified'}, conf {d.confidence:.2f})"
                (fails if d.confidence >= self.cfg.min_confidence else reviews).append(msg)
        if fails:
            return PolicyResult(Verdict.FAIL, fails)

        if not r.looks_authentic:
            reviews.append("may be a proxy/custom/reprint")
        if r.scope == "back_only":
            if not r.back_visible:
                reviews.append("no usable back photo (can't check whitening)")
            elif r.image_quality == "insufficient":
                reviews.append("photos insufficient to judge")
        elif r.image_quality == "insufficient" or not r.front_visible:
            reviews.append("photos insufficient to judge")
        elif self.cfg.require_back_photo and not r.back_visible:
            reviews.append("no back photo (can't check whitening)")
        if len(r.unassessed_zones) > self.cfg.max_unassessed_zones:
            reviews.append(f"glare/obstruction hides {len(r.unassessed_zones)} edge zones: {', '.join(r.unassessed_zones)}")
        if r.overall == "unclear":
            reviews.append("model could not determine condition")

        if reviews:
            return PolicyResult(Verdict.REVIEW, reviews)
        return PolicyResult(Verdict.PASS, [])
